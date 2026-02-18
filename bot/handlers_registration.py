from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.fsm_states import RegistrationFSM


class OCRClientProtocol(Protocol):
    async def submit(self, *, document_url: str, correlation_id: str) -> Any: ...

    async def get_job(self, *, job_id: str, correlation_id: str) -> Any: ...


class CRMConnectorProtocol(Protocol):
    async def find_duplicate(self, *, passport_hash: str, correlation_id: str) -> bool: ...

    async def create_registration(self, *, payload: dict[str, Any], correlation_id: str) -> dict[str, Any]: ...

    async def trigger_manager(self, *, reason: str, payload: dict[str, Any], correlation_id: str) -> None: ...


@dataclass(slots=True)
class DeepLinkManager:
    secret: str

    def issue_token(self, *, manager_id: str) -> str:
        body = manager_id.encode()
        sig = hmac.new(self.secret.encode(), body, hashlib.sha256).digest()[:8]
        raw = body + b":" + sig
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    def parse_token(self, token: str) -> str | None:
        padded = token + "=" * ((4 - len(token) % 4) % 4)
        try:
            raw = base64.urlsafe_b64decode(padded.encode())
            manager_raw, signature = raw.rsplit(b":", 1)
        except Exception:
            return None
        expected = hmac.new(self.secret.encode(), manager_raw, hashlib.sha256).digest()[:8]
        if not hmac.compare_digest(signature, expected):
            return None
        return manager_raw.decode()


@dataclass(slots=True)
class RegistrationFlow:
    ocr_client: OCRClientProtocol
    crm_connector: CRMConnectorProtocol
    deep_link_manager: DeepLinkManager
    poll_interval_sec: float = 0.0
    max_polls: int = 3

    @staticmethod
    def _mask(value: str | None) -> str:
        if not value:
            return ""
        if len(value) <= 4:
            return "*" * len(value)
        return value[:2] + "*" * (len(value) - 4) + value[-2:]

    @staticmethod
    def _preview(data: dict[str, Any]) -> str:
        fields = data.get("ocr_fields", {})
        return "\n".join(
            [
                "Проверьте данные:",
                f"Имя: {RegistrationFlow._mask(fields.get('name'))}",
                f"Документ: {RegistrationFlow._mask(fields.get('document_number'))}",
                f"Дата рождения: {RegistrationFlow._mask(fields.get('birth_date'))}",
                "Действия: confirm / edit / rescan",
            ]
        )

    async def start(self, state: FSMContext, *, manager_token: str | None) -> dict[str, Any]:
        manager_id = self.deep_link_manager.parse_token(manager_token) if manager_token else None
        correlation_id = str(uuid4())
        data = {
            "correlation_id": correlation_id,
            "manager_id": manager_id,
            "events": [],
            "ocr_fields": {},
            "flags": {},
        }
        await state.set_data(data)
        await state.set_state(RegistrationFSM.COLLECT_CONTACT)
        return {"state": "COLLECT_CONTACT", "manager_id": manager_id, "correlation_id": correlation_id}

    async def collect_contact(self, state: FSMContext, *, phone: str) -> dict[str, Any]:
        data = await state.get_data()
        data["phone"] = phone
        await state.set_data(data)
        await state.set_state(RegistrationFSM.UPLOAD_DOC)
        return {"state": "UPLOAD_DOC"}

    async def upload_doc(self, state: FSMContext, *, document_url: str) -> dict[str, Any]:
        data = await state.get_data()
        data["document_url"] = document_url
        await state.set_data(data)
        await state.set_state(RegistrationFSM.QUALITY_PRECHECK)
        return {"state": "QUALITY_PRECHECK"}

    async def quality_precheck(self, state: FSMContext, *, is_valid: bool) -> dict[str, Any]:
        data = await state.get_data()
        if not is_valid:
            data["flags"]["invalid_mrz"] = True
            await state.set_data(data)
            await state.set_state(RegistrationFSM.UPLOAD_DOC)
            return {"error": "invalid MRZ", "state": "UPLOAD_DOC"}
        await state.set_state(RegistrationFSM.OCR_SUBMIT)
        return {"state": "OCR_SUBMIT"}

    async def ocr_submit(self, state: FSMContext) -> dict[str, Any]:
        data = await state.get_data()
        correlation_id = data["correlation_id"]
        submit = await self.ocr_client.submit(document_url=data["document_url"], correlation_id=correlation_id)
        data["ocr_job_id"] = submit.job_id
        await state.set_data(data)
        await state.set_state(RegistrationFSM.WAIT_RESULT)
        return {"state": "WAIT_RESULT", "job_id": submit.job_id}

    async def wait_result(self, state: FSMContext) -> dict[str, Any]:
        data = await state.get_data()
        corr = data["correlation_id"]
        job_id = data["ocr_job_id"]

        for _ in range(self.max_polls):
            job = await self.ocr_client.get_job(job_id=job_id, correlation_id=corr)
            status = getattr(job, "status", "")
            if status in {"pending", "processing"}:
                if self.poll_interval_sec:
                    await asyncio.sleep(self.poll_interval_sec)
                continue
            if status == "failed":
                data["flags"]["ocr_fail"] = True
                await state.set_data(data)
                await state.set_state(RegistrationFSM.UPLOAD_DOC)
                return {"error": "OCR fail", "state": "UPLOAD_DOC"}
            if status == "timeout":
                data["flags"]["timeout"] = True
                await self.crm_connector.trigger_manager(reason="sla_breach", payload=data, correlation_id=corr)
                await state.set_data(data)
                await state.set_state(RegistrationFSM.MANAGER_VERIFICATION)
                return {"error": "timeout", "state": "MANAGER_VERIFICATION"}

            result = getattr(job, "result", None) or {}
            reason = getattr(job, "reason", None)
            if reason == "duplicate":
                await self.crm_connector.trigger_manager(reason="duplicate_detected", payload=result, correlation_id=corr)
                data["flags"]["duplicate"] = True
                await state.set_data(data)
                await state.set_state(RegistrationFSM.MANAGER_VERIFICATION)
                return {"error": "duplicate", "state": "MANAGER_VERIFICATION"}
            if reason == "manual_review":
                await self.crm_connector.trigger_manager(reason="manual_review", payload=result, correlation_id=corr)
                data["ocr_fields"] = result
                await state.set_data(data)
                await state.set_state(RegistrationFSM.MANUAL_EDIT)
                return {"state": "MANUAL_EDIT"}

            data["ocr_fields"] = result
            await state.set_data(data)
            await state.set_state(RegistrationFSM.PREVIEW_CONFIRM)
            return {"state": "PREVIEW_CONFIRM", "preview": self._preview(data)}

        data["flags"]["timeout"] = True
        await self.crm_connector.trigger_manager(reason="sla_breach", payload=data, correlation_id=corr)
        await state.set_data(data)
        await state.set_state(RegistrationFSM.MANAGER_VERIFICATION)
        return {"error": "timeout", "state": "MANAGER_VERIFICATION"}

    async def preview_action(self, state: FSMContext, *, action: str) -> dict[str, Any]:
        data = await state.get_data()
        if action == "rescan":
            await state.set_state(RegistrationFSM.UPLOAD_DOC)
            return {"state": "UPLOAD_DOC"}
        if action == "edit":
            await state.set_state(RegistrationFSM.MANUAL_EDIT)
            return {"state": "MANUAL_EDIT"}
        if action != "confirm":
            return {"error": "unknown action"}

        await state.set_state(RegistrationFSM.CONFIRMED)
        corr = data["correlation_id"]
        if data["ocr_fields"].get("passport_hash"):
            duplicate = await self.crm_connector.find_duplicate(
                passport_hash=data["ocr_fields"]["passport_hash"],
                correlation_id=corr,
            )
            if duplicate:
                await self.crm_connector.trigger_manager(reason="duplicate_detected", payload=data, correlation_id=corr)
                await state.set_state(RegistrationFSM.MANAGER_VERIFICATION)
                return {"error": "duplicate", "state": "MANAGER_VERIFICATION"}

        result = await self.crm_connector.create_registration(payload=data, correlation_id=corr)
        data["crm"] = result
        await state.set_data(data)
        await state.set_state(RegistrationFSM.DONE)
        return {"state": "DONE", "crm": result}

    async def manual_edit(self, state: FSMContext, *, patch: dict[str, str]) -> dict[str, Any]:
        data = await state.get_data()
        data["ocr_fields"].update(patch)
        await state.set_data(data)
        await state.set_state(RegistrationFSM.PREVIEW_CONFIRM)
        return {"state": "PREVIEW_CONFIRM", "preview": self._preview(data)}


def create_registration_router(flow: RegistrationFlow) -> Router:
    router = Router(name="registration_v3")

    @router.message(CommandStart(deep_link=True))
    async def cmd_start(message: Message, state: FSMContext, command: CommandStart) -> None:
        result = await flow.start(state, manager_token=command.args)
        await message.answer(json.dumps(result, ensure_ascii=False))

    @router.message(RegistrationFSM.COLLECT_CONTACT)
    async def on_contact(message: Message, state: FSMContext) -> None:
        result = await flow.collect_contact(state, phone=message.text or "")
        await message.answer(json.dumps(result, ensure_ascii=False))

    @router.message(RegistrationFSM.UPLOAD_DOC)
    async def on_doc(message: Message, state: FSMContext) -> None:
        result = await flow.upload_doc(state, document_url=message.text or "")
        await message.answer(json.dumps(result, ensure_ascii=False))

    @router.callback_query(RegistrationFSM.PREVIEW_CONFIRM, F.data.in_({"confirm", "edit", "rescan"}))
    async def on_preview_action(callback: CallbackQuery, state: FSMContext) -> None:
        result = await flow.preview_action(state, action=callback.data or "")
        await callback.message.answer(json.dumps(result, ensure_ascii=False))
        await callback.answer()

    return router
