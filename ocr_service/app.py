from __future__ import annotations

try:
    from fastapi import FastAPI, HTTPException
except ModuleNotFoundError:  # pragma: no cover
    from .fastapi_compat import FastAPI, HTTPException

from .adapters import FallbackOCRAdapter, HttpCRMConnector, LocalOCRAdapter, StorageAdapter
from .engines import QualityAnalyzer, RetryEngine, SLAEngine
from .logging import configure_logging
from .models import JobResponse, ManualReviewRequest, SubmitOCRRequest, SubmitOCRResponse, WebhookResult
from .orchestrator import OcrOrchestrator
from .repository import JobRepository
from .settings import settings

configure_logging()

app = FastAPI(title="OCR Orchestrator", version="1.0.0")

repo = JobRepository()
orchestrator = OcrOrchestrator(
    repo=repo,
    storage=StorageAdapter(),
    local_adapter=LocalOCRAdapter(timeout_seconds=settings.local_timeout),
    fallback_adapter=FallbackOCRAdapter(timeout_seconds=settings.fallback_timeout),
    sla_engine=SLAEngine(settings),
    quality=QualityAnalyzer(),
    retry=RetryEngine(),
    crm=HttpCRMConnector(retries=settings.crm_retry_attempts, backoff=settings.crm_retry_backoff_seconds),
    settings=settings,
)


@app.post("/v1/ocr/submit", response_model=SubmitOCRResponse)
async def submit_ocr(payload: SubmitOCRRequest) -> SubmitOCRResponse:
    if not payload.correlation_id:
        raise HTTPException(status_code=422, detail="correlation_id is required")
    job_id = await orchestrator.submit(str(payload.media_url), payload.correlation_id)
    return SubmitOCRResponse(job_id=job_id)


@app.get("/v1/ocr/job/{job_id}", response_model=JobResponse)
async def get_job(job_id: str) -> JobResponse:
    job = repo.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return JobResponse(
        job_id=job.job_id,
        status=job.status,
        correlation_id=job.correlation_id,
        cycle_count=job.cycle_count,
        result=job.result,
        audit_trail=job.audit_trail,
    )


@app.post("/v1/ocr/manual-review/{job_id}", response_model=JobResponse)
async def manual_review(job_id: str, payload: ManualReviewRequest) -> JobResponse:
    try:
        job = await orchestrator.manual_review(job_id, payload.corrections)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="job not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return JobResponse(
        job_id=job.job_id,
        status=job.status,
        correlation_id=job.correlation_id,
        cycle_count=job.cycle_count,
        result=job.result,
        audit_trail=job.audit_trail,
    )


@app.post("/internal/webhooks/ocr-result")
async def ocr_webhook(payload: WebhookResult) -> dict[str, str]:
    return {"status": "accepted", "job_id": payload.job_id}
