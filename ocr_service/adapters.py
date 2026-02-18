from __future__ import annotations

import abc
import asyncio
import hashlib
from typing import Any

import requests

from .models import MRZData
from .mrz_parser import MRZParser


class OCRAdapter(abc.ABC):
    @abc.abstractmethod
    async def extract(self, content: bytes, correlation_id: str) -> dict[str, Any]:
        raise NotImplementedError


class LocalOCRAdapter(OCRAdapter):
    def __init__(self, timeout_seconds: int = 2):
        self.timeout_seconds = timeout_seconds
        self.parser = MRZParser()

    async def extract(self, content: bytes, correlation_id: str) -> dict[str, Any]:
        async def _work() -> dict[str, Any]:
            text = content.decode("utf-8", errors="ignore")
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            mrz = self.parser.parse(lines[-3:])
            return {"text": text, "mrz": mrz}

        return await asyncio.wait_for(_work(), timeout=self.timeout_seconds)


class FallbackOCRAdapter(OCRAdapter):
    def __init__(self, timeout_seconds: int = 5):
        self.timeout_seconds = timeout_seconds
        self.parser = MRZParser()

    async def extract(self, content: bytes, correlation_id: str) -> dict[str, Any]:
        async def _work() -> dict[str, Any]:
            text = content.decode("utf-8", errors="ignore")
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            mrz = self.parser.parse(lines[-3:])
            return {"text": text, "mrz": mrz}

        return await asyncio.wait_for(_work(), timeout=self.timeout_seconds)


class StorageAdapter:
    async def fetch_content(self, media_url: str) -> tuple[bytes, str]:
        def _download() -> bytes:
            response = requests.get(media_url, timeout=6)
            response.raise_for_status()
            return response.content

        content = await asyncio.to_thread(_download)
        checksum = hashlib.sha256(content).hexdigest()
        return content, checksum


class CRMConnector(abc.ABC):
    @abc.abstractmethod
    async def create_or_update_resident(self, *, correlation_id: str, mrz: MRZData) -> dict[str, Any]:
        raise NotImplementedError

    @abc.abstractmethod
    async def attach_document_links(self, *, correlation_id: str, links: list[str]) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    async def send_webhook_result(self, payload: dict[str, Any]) -> None:
        raise NotImplementedError


class HttpCRMConnector(CRMConnector):
    def __init__(self, endpoint: str = "http://localhost/internal/webhooks/ocr-result", retries: int = 3, backoff: float = 0.1):
        self.endpoint = endpoint
        self.retries = retries
        self.backoff = backoff

    async def create_or_update_resident(self, *, correlation_id: str, mrz: MRZData) -> dict[str, Any]:
        return {"resident_id": mrz.passport_hash, "correlation_id": correlation_id}

    async def attach_document_links(self, *, correlation_id: str, links: list[str]) -> None:
        return None

    async def send_webhook_result(self, payload: dict[str, Any]) -> None:
        attempts = 0
        last_err: Exception | None = None
        while attempts < self.retries:
            attempts += 1
            try:
                await asyncio.to_thread(requests.post, self.endpoint, json=payload, timeout=3)
                return
            except Exception as exc:  # nosec B110
                last_err = exc
                await asyncio.sleep(self.backoff * attempts)
        if last_err:
            raise last_err
