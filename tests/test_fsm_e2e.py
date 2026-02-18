import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dataclasses import dataclass
from typing import Any

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from bot.fsm_states import RegistrationFSM
from bot.handlers_registration import DeepLinkManager, RegistrationFlow


@dataclass
class _SubmitResp:
    job_id: str


@dataclass
class _JobResp:
    status: str
    result: dict[str, Any] | None
    reason: str | None = None


class FakeOCRClient:
    def __init__(self, jobs: list[_JobResp]) -> None:
        self.jobs = jobs
        self.submit_calls: list[dict[str, str]] = []
        self.get_calls: list[dict[str, str]] = []

    async def submit(self, *, document_url: str, correlation_id: str) -> _SubmitResp:
        self.submit_calls.append({"document_url": document_url, "correlation_id": correlation_id})
        return _SubmitResp(job_id="job-1")

    async def get_job(self, *, job_id: str, correlation_id: str) -> _JobResp:
        self.get_calls.append({"job_id": job_id, "correlation_id": correlation_id})
        if self.jobs:
            return self.jobs.pop(0)
        return _JobResp(status="done", result={"name": "JOHN DOE"})


class FakeCRM:
    def __init__(self, *, duplicate=False) -> None:
        self.duplicate = duplicate
        self.manager_events: list[tuple[str, str]] = []
        self.created: list[dict[str, Any]] = []

    async def find_duplicate(self, *, passport_hash: str, correlation_id: str) -> bool:
        return self.duplicate

    async def create_registration(self, *, payload: dict[str, Any], correlation_id: str) -> dict[str, Any]:
        self.created.append({"payload": payload, "correlation_id": correlation_id})
        return {"lead_id": 101}

    async def trigger_manager(self, *, reason: str, payload: dict[str, Any], correlation_id: str) -> None:
        self.manager_events.append((reason, correlation_id))


def _ctx(storage: MemoryStorage) -> FSMContext:
    return FSMContext(storage=storage, key=StorageKey(bot_id=42, chat_id=77, user_id=77))


def test_fsm_happy_path_and_resume_after_restart_and_masking():
    async def _run() -> None:
        storage = MemoryStorage()
        context_1 = _ctx(storage)
        deep_link = DeepLinkManager(secret="top-secret")
        token = deep_link.issue_token(manager_id="mgr-1")

        ocr = FakeOCRClient(
            jobs=[
                _JobResp(
                    status="done",
                    result={
                        "name": "IVAN IVANOV",
                        "document_number": "AA1234567",
                        "birth_date": "1990-01-01",
                        "passport_hash": "hash-ok",
                    },
                )
            ]
        )
        crm = FakeCRM(duplicate=False)
        flow = RegistrationFlow(ocr_client=ocr, crm_connector=crm, deep_link_manager=deep_link)

        started = await flow.start(context_1, manager_token=token)
        assert started["state"] == "COLLECT_CONTACT"
        assert started["manager_id"] == "mgr-1"

        await flow.collect_contact(context_1, phone="+79990001122")
        await flow.upload_doc(context_1, document_url="https://doc.local/p1.jpg")
        await flow.quality_precheck(context_1, is_valid=True)
        await flow.ocr_submit(context_1)
        preview = await flow.wait_result(context_1)

        assert preview["state"] == "PREVIEW_CONFIRM"
        assert "IVAN IVANOV" not in preview["preview"]
        assert "AA1234567" not in preview["preview"]
        assert "*" in preview["preview"]

        # Simulate process restart: same storage + same storage key => FSM state/data recovered.
        context_after_restart = _ctx(storage)
        current_state = await context_after_restart.get_state()
        assert current_state == RegistrationFSM.PREVIEW_CONFIRM.state

        done = await flow.preview_action(context_after_restart, action="confirm")
        assert done["state"] == "DONE"
        assert crm.created[0]["correlation_id"] == started["correlation_id"]
        assert ocr.submit_calls[0]["correlation_id"] == started["correlation_id"]
        assert ocr.get_calls[0]["correlation_id"] == started["correlation_id"]

    asyncio.run(_run())


def test_fsm_duplicate_path_triggers_manager_verification():
    async def _run() -> None:
        storage = MemoryStorage()
        context = _ctx(storage)
        deep_link = DeepLinkManager(secret="top-secret")
        ocr = FakeOCRClient(jobs=[_JobResp(status="done", result={"passport_hash": "dup"}, reason="duplicate")])
        crm = FakeCRM()
        flow = RegistrationFlow(ocr_client=ocr, crm_connector=crm, deep_link_manager=deep_link)

        await flow.start(context, manager_token=None)
        await flow.collect_contact(context, phone="+79990001122")
        await flow.upload_doc(context, document_url="https://doc.local/p1.jpg")
        await flow.quality_precheck(context, is_valid=True)
        await flow.ocr_submit(context)
        result = await flow.wait_result(context)

        assert result["state"] == "MANAGER_VERIFICATION"
        assert result["error"] == "duplicate"
        assert crm.manager_events[0][0] == "duplicate_detected"

    asyncio.run(_run())


def test_fsm_manual_review_path():
    async def _run() -> None:
        storage = MemoryStorage()
        context = _ctx(storage)
        deep_link = DeepLinkManager(secret="top-secret")
        ocr = FakeOCRClient(
            jobs=[
                _JobResp(
                    status="done",
                    result={"name": "RAW", "document_number": "RAW123"},
                    reason="manual_review",
                )
            ]
        )
        crm = FakeCRM()
        flow = RegistrationFlow(ocr_client=ocr, crm_connector=crm, deep_link_manager=deep_link)

        await flow.start(context, manager_token=None)
        await flow.collect_contact(context, phone="+79990001122")
        await flow.upload_doc(context, document_url="https://doc.local/p1.jpg")
        await flow.quality_precheck(context, is_valid=True)
        await flow.ocr_submit(context)
        to_manual = await flow.wait_result(context)

        assert to_manual["state"] == "MANUAL_EDIT"
        edited = await flow.manual_edit(context, patch={"name": "EDITED USER"})
        assert edited["state"] == "PREVIEW_CONFIRM"
        assert "EDITED USER" not in edited["preview"]  # must stay masked

    asyncio.run(_run())
