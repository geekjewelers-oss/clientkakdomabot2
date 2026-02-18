from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Protocol

import structlog
from pydantic import ConfigDict
from pydantic.dataclasses import dataclass as pydantic_dataclass

from .models import DecisionTraceEntry


@pydantic_dataclass(config=ConfigDict(extra="forbid", strict=True))
class ChecklistAuditRecord:
    correlation_id: str
    resident_id: str
    checklist_version: str
    decisions: list[DecisionTraceEntry]
    override_flag: bool
    timestamp: datetime


class AuditSink(Protocol):
    """Append-only sink for checklist audit records."""

    def append(self, record: ChecklistAuditRecord) -> str:
        ...


@dataclass
class InMemoryAuditSink:
    records: dict[str, ChecklistAuditRecord]

    def append(self, record: ChecklistAuditRecord) -> str:
        audit_id = sha256(f"{record.correlation_id}:{record.resident_id}:{record.timestamp.isoformat()}".encode()).hexdigest()
        self.records[audit_id] = record
        return audit_id


@dataclass
class AuditLogger:
    sink: AuditSink
    logger: structlog.stdlib.BoundLogger

    def create_record(
        self,
        *,
        correlation_id: str,
        resident_id: str,
        checklist_version: str,
        decisions: list[DecisionTraceEntry],
        override_flag: bool,
    ) -> str:
        record = ChecklistAuditRecord(
            correlation_id=correlation_id,
            resident_id=resident_id,
            checklist_version=checklist_version,
            decisions=decisions,
            override_flag=override_flag,
            timestamp=datetime.now(timezone.utc),
        )
        audit_id = self.sink.append(record)
        self.logger.info(
            "checklist_audit_record",
            audit_id=audit_id,
            correlation_id=correlation_id,
            resident_id=resident_id,
            checklist_version=checklist_version,
            override_flag=override_flag,
            decisions=[self._masked_decision(decision) for decision in decisions],
            timestamp=record.timestamp.isoformat(),
        )
        return audit_id

    @staticmethod
    def _masked_decision(decision: DecisionTraceEntry) -> dict[str, str]:
        payload = decision.model_dump()
        input_payload = dict(payload["input"])
        if "passport_hash" in input_payload:
            input_payload["passport_hash"] = "***"
        payload["input"] = input_payload
        payload["timestamp"] = decision.timestamp.isoformat()
        return payload
