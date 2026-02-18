import asyncio

import ocr_service.orchestrator as orchestrator_module
from ocr_service.app import (
    get_job,
    manual_review,
    orchestrator,
    repo,
    submit_ocr,
)
from ocr_service.models import JobStatus, ManualReviewRequest, SubmitOCRRequest


class DummyCRM:
    async def create_or_update_resident(self, **kwargs):
        return {"ok": True, **kwargs}

    async def attach_document_links(self, **kwargs):
        return None

    async def send_webhook_result(self, payload):
        return None


async def _prepare_success_job(*, reset: bool = True):
    if reset:
        repo.jobs.clear()
        repo.mrz_index.clear()
    orchestrator.crm = DummyCRM()

    async def fake_fetch(_url: str):
        text = "\n".join(
            [
                "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<",
                "L898902C36UTO7408122F1204159ZE184226B<<<<<10",
            ]
        )
        return text.encode(), "hash1"

    orchestrator.storage.fetch_content = fake_fetch
    req = SubmitOCRRequest(media_url="https://example.com/doc", correlation_id="corr-12345678")
    original_create_task = orchestrator_module.asyncio.create_task
    orchestrator_module.asyncio.create_task = lambda coro: (coro.close(), None)[1]
    try:
        submit = await submit_ocr(req)
    finally:
        orchestrator_module.asyncio.create_task = original_create_task
    await orchestrator.process_job(submit.job_id)
    return submit.job_id


def test_submit_and_get_job_flow():
    job_id = asyncio.run(_prepare_success_job(reset=True))
    data = asyncio.run(get_job(job_id))

    assert data.status == JobStatus.auto_accepted
    assert data.result is not None
    assert data.result.mrz.checksum_ok is True


def test_manual_review_endpoint():
    job_id = asyncio.run(_prepare_success_job(reset=True))
    repo.get(job_id).status = JobStatus.manual_review

    data = asyncio.run(
        manual_review(
            job_id,
            ManualReviewRequest(correlation_id="corr-12345678", corrections={"surname": "IVANOV"}),
        )
    )

    assert data.result is not None
    assert data.result.mrz.surname == "IVANOV"


def test_duplicate_detected():
    asyncio.run(_prepare_success_job(reset=True))
    job_id2 = asyncio.run(_prepare_success_job(reset=False))

    job2 = repo.get(job_id2)
    assert job2 is not None
    assert job2.status == JobStatus.duplicate_detected
