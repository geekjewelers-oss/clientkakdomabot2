from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(slots=True)
class OCRSubmitResponse:
    job_id: str


@dataclass(slots=True)
class OCRJobResponse:
    status: str
    result: dict[str, Any] | None
    reason: str | None = None


class OCRClient:
    """Async OCR orchestrator client for /v1/ocr/submit and /v1/ocr/job/{id}."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout: float = 10.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(timeout=timeout, base_url=base_url.rstrip("/"))

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def submit(self, *, document_url: str, correlation_id: str) -> OCRSubmitResponse:
        response = await self._client.post(
            "/v1/ocr/submit",
            json={"media_url": document_url, "correlation_id": correlation_id},
            headers={"X-Correlation-ID": correlation_id},
        )
        response.raise_for_status()
        payload = response.json()
        return OCRSubmitResponse(job_id=str(payload["job_id"]))

    async def get_job(self, *, job_id: str, correlation_id: str) -> OCRJobResponse:
        response = await self._client.get(
            f"/v1/ocr/job/{job_id}",
            headers={"X-Correlation-ID": correlation_id},
        )
        response.raise_for_status()
        payload = response.json()
        return OCRJobResponse(
            status=str(payload.get("status", "unknown")),
            result=payload.get("result"),
            reason=payload.get("reason"),
        )
