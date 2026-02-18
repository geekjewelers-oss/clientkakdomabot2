from __future__ import annotations

from datetime import datetime, timezone

from .models import JobRecord


class JobRepository:
    def __init__(self) -> None:
        self.jobs: dict[str, JobRecord] = {}
        self.mrz_index: dict[str, str] = {}

    def add(self, job: JobRecord) -> None:
        self.jobs[job.job_id] = job

    def get(self, job_id: str) -> JobRecord | None:
        return self.jobs.get(job_id)

    def update(self, job: JobRecord) -> None:
        job.updated_at = datetime.now(tz=timezone.utc)
        self.jobs[job.job_id] = job

    def add_audit(self, job: JobRecord, event: str, details: dict) -> None:
        job.audit_trail.append(
            {
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                "event": event,
                "details": details,
            }
        )
        self.update(job)

    def check_duplicate(self, passport_hash: str) -> bool:
        return passport_hash in self.mrz_index

    def register_passport_hash(self, passport_hash: str, job_id: str) -> None:
        self.mrz_index[passport_hash] = job_id
