from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import logging

try:
    import structlog
except ModuleNotFoundError:  # pragma: no cover
    structlog = None

from .adapters import CRMConnector, FallbackOCRAdapter, LocalOCRAdapter, StorageAdapter
from .engines import QualityAnalyzer, RetryEngine, SLAEngine
from .models import JobRecord, JobStatus, OCRResult
from .repository import JobRepository
from .settings import OCRSettings


class OcrOrchestrator:
    def __init__(
        self,
        *,
        repo: JobRepository,
        storage: StorageAdapter,
        local_adapter: LocalOCRAdapter,
        fallback_adapter: FallbackOCRAdapter,
        sla_engine: SLAEngine,
        quality: QualityAnalyzer,
        retry: RetryEngine,
        crm: CRMConnector,
        settings: OCRSettings,
    ) -> None:
        self.repo = repo
        self.storage = storage
        self.local_adapter = local_adapter
        self.fallback_adapter = fallback_adapter
        self.sla_engine = sla_engine
        self.quality = quality
        self.retry = retry
        self.crm = crm
        self.settings = settings
        self.logger = structlog.get_logger("ocr_orchestrator") if structlog else logging.getLogger("ocr_orchestrator")

    async def submit(self, media_url: str, correlation_id: str) -> str:
        job = JobRecord(media_url=media_url, correlation_id=correlation_id)
        self.repo.add(job)
        self.repo.add_audit(job, "submitted", {"correlation_id": correlation_id})
        asyncio.create_task(self.process_job(job.job_id))
        return job.job_id

    async def process_job(self, job_id: str) -> None:
        job = self.repo.get(job_id)
        if not job:
            return
        started = datetime.now(tz=timezone.utc)
        job.status = JobStatus.processing
        self.repo.add_audit(job, "processing_started", {})

        content, content_hash = await self.storage.fetch_content(job.media_url)
        job.content_hash = content_hash

        for _ in range(self.settings.local_attempts):
            job.cycle_count = self.retry.next_cycle(job.cycle_count)
            local = await self.local_adapter.extract(content, job.correlation_id)
            mrz = local["mrz"]
            quality = self.quality.analyze(content=content, confidence=mrz.confidence)
            decision = self.sla_engine.decide(mrz=mrz, cycle_count=job.cycle_count)
            self.repo.add_audit(job, "local_attempt", {"cycle_count": job.cycle_count, "decision": decision.status.value})

            if decision.use_fallback:
                for _ in range(self.settings.fallback_attempts):
                    fallback = await self.fallback_adapter.extract(content, job.correlation_id)
                    f_mrz = fallback["mrz"]
                    f_quality = self.quality.analyze(content=content, confidence=f_mrz.confidence)
                    decision = self.sla_engine.decide(mrz=f_mrz, cycle_count=job.cycle_count)
                    self.repo.add_audit(job, "fallback_attempt", {"decision": decision.status.value})
                    local = fallback
                    mrz = f_mrz
                    quality = f_quality
                    if decision.status != JobStatus.processing:
                        break

            duplicate = bool(mrz.passport_hash and self.repo.check_duplicate(mrz.passport_hash))
            result = OCRResult(quality=quality, mrz=mrz, text=local["text"], duplicate_detected=duplicate)
            job.result = result

            if duplicate:
                job.status = JobStatus.duplicate_detected
                self.repo.add_audit(job, "duplicate_detected", {"passport_hash": mrz.passport_hash})
                self.repo.update(job)
                await self._notify_crm(job)
                return

            if decision.status in {JobStatus.auto_accepted, JobStatus.manual_review}:
                job.status = decision.status
                if decision.status == JobStatus.auto_accepted and mrz.passport_hash:
                    self.repo.register_passport_hash(mrz.passport_hash, job.job_id)
                self.repo.update(job)
                await self._notify_crm(job)
                return

        job.status = JobStatus.failed
        self.repo.add_audit(job, "failed", {"reason": "sla_exhausted"})
        self.repo.update(job)

        elapsed = (datetime.now(tz=timezone.utc) - started).total_seconds()
        if self.settings.sla_breach_flag and elapsed > self.settings.total_timeout:
            self.repo.add_audit(job, "sla_breach", {"elapsed": elapsed})

    async def manual_review(self, job_id: str, corrections: dict) -> JobRecord:
        job = self.repo.get(job_id)
        if not job:
            raise KeyError(job_id)
        if not job.result:
            raise ValueError("missing OCR result")
        for field, value in corrections.items():
            if hasattr(job.result.mrz, field):
                setattr(job.result.mrz, field, value)
        job.status = JobStatus.auto_accepted
        self.repo.add_audit(job, "manual_review_applied", {"fields": list(corrections)})
        self.repo.update(job)
        await self._notify_crm(job)
        return job

    async def _notify_crm(self, job: JobRecord) -> None:
        if not job.result:
            return
        await self.crm.create_or_update_resident(correlation_id=job.correlation_id, mrz=job.result.mrz)
        await self.crm.attach_document_links(correlation_id=job.correlation_id, links=[job.media_url])
        await self.crm.send_webhook_result(
            {
                "job_id": job.job_id,
                "correlation_id": job.correlation_id,
                "status": job.status.value,
                "payload": {
                    "passport_hash": job.result.mrz.passport_hash,
                    "duplicate_detected": job.result.duplicate_detected,
                },
            }
        )
