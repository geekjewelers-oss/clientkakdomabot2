import asyncio
from collections import deque

import httpx
import pytest

from connectors.bitrix_connector import BitrixConnector
from schemas.bitrix_models import BitrixTenantCredentials, DealData, OCRBitrixFields, ResidentData


def _tenant() -> dict[str, BitrixTenantCredentials]:
    return {
        "tenant-a": BitrixTenantCredentials(
            tenant_id="tenant-a",
            webhook_base_url="https://bitrix.example/rest/1/abc",
        )
    }


def _resident(idempotency_key: str | None = None) -> ResidentData:
    return ResidentData(
        tenant_id="tenant-a",
        correlation_id="corr-12345678",
        idempotency_key=idempotency_key,
        first_name="Ivan",
        last_name="Petrov",
        phone="+79001112233",
        ocr=OCRBitrixFields(
            passport_hash="sha256:deadbeefcafebabe",
            nationality="RU",
            birth_date="1990-01-01",
            doc_expiry="2030-01-01",
            ocr_confidence=0.97,
            duplicate_flag=False,
        ),
    )


def _deal() -> DealData:
    return DealData(
        tenant_id="tenant-a",
        correlation_id="corr-12345678",
        title="Registration lead",
        amount=10000,
        currency="RUB",
        stage_id="NEW",
        contact_id=321,
        ocr=OCRBitrixFields(
            passport_hash="sha256:deadbeefcafebabe",
            nationality="RU",
            birth_date="1990-01-01",
            doc_expiry="2030-01-01",
            ocr_confidence=0.91,
            duplicate_flag=True,
        ),
    )


def test_create_contact_success_and_strict_mapping():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["json"] = request.read().decode()
        return httpx.Response(200, json={"result": 123})

    transport = httpx.MockTransport(handler)
    connector = BitrixConnector(_tenant(), transport=transport)

    result = asyncio.run(connector.create_contact(_resident()))
    asyncio.run(connector.aclose())

    assert result == 123
    assert captured["headers"]["x-correlation-id"] == "corr-12345678"
    body = captured["json"]
    assert "UF_PASSPORT_HASH" in body
    assert "UF_NATIONALITY" in body
    assert "UF_BIRTH_DATE" in body
    assert "UF_DOC_EXPIRY" in body
    assert "UF_OCR_CONFIDENCE" in body
    assert "UF_DUPLICATE_FLAG" in body
    assert "passport_number" not in body


def test_retry_on_429_with_retry_after(monkeypatch: pytest.MonkeyPatch):
    calls = {"n": 0}
    sleeps = []

    async def fake_sleep(seconds: float):
        sleeps.append(seconds)

    monkeypatch.setattr("connectors.bitrix_connector.asyncio.sleep", fake_sleep)

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": "too_many_requests"})
        return httpx.Response(200, json={"result": 124})

    connector = BitrixConnector(_tenant(), transport=httpx.MockTransport(handler))
    result = asyncio.run(connector.create_contact(_resident()))
    asyncio.run(connector.aclose())

    assert result == 124
    assert calls["n"] == 2
    assert sleeps == [0.0]


def test_retry_on_5xx(monkeypatch: pytest.MonkeyPatch):
    calls = {"n": 0}

    async def fake_sleep(_seconds: float):
        return None

    monkeypatch.setattr("connectors.bitrix_connector.asyncio.sleep", fake_sleep)

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, json={"error": "bad_gateway"})
        return httpx.Response(200, json={"result": 125})

    connector = BitrixConnector(_tenant(), transport=httpx.MockTransport(handler))
    result = asyncio.run(connector.create_contact(_resident()))
    asyncio.run(connector.aclose())

    assert result == 125
    assert calls["n"] == 3


def test_retry_on_network_error(monkeypatch: pytest.MonkeyPatch):
    calls = {"n": 0}

    async def fake_sleep(_seconds: float):
        return None

    monkeypatch.setattr("connectors.bitrix_connector.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadTimeout("timeout", request=request)
        return httpx.Response(200, json={"result": 126})

    connector = BitrixConnector(_tenant(), transport=httpx.MockTransport(handler))
    result = asyncio.run(connector.create_contact(_resident()))
    asyncio.run(connector.aclose())

    assert result == 126
    assert calls["n"] == 2


def test_duplicate_detection_path_and_idempotency_cache():
    queue = deque(
        [
            httpx.Response(200, json={"result": [{"ID": "10", "UF_PASSPORT_HASH": "sha256:deadbeefcafebabe"}]}),
            httpx.Response(200, json={"result": 990}),
        ]
    )
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return queue.popleft()

    connector = BitrixConnector(_tenant(), transport=httpx.MockTransport(handler))

    found = asyncio.run(
        connector.search_by_passport_hash(
            tenant_id="tenant-a", correlation_id="corr-12345678", passport_hash="sha256:deadbeefcafebabe"
        )
    )
    assert len(found) == 1

    resident = _resident(idempotency_key="idem-1")
    first = asyncio.run(connector.create_contact(resident))
    second = asyncio.run(connector.create_contact(resident))
    asyncio.run(connector.aclose())

    assert first == 990
    assert second == 990
    assert calls["n"] == 2


def test_create_lead_and_management_methods_integration_style():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, request.headers.get("x-correlation-id"), request.read().decode()))
        if request.url.path.endswith("crm.lead.add.json"):
            return httpx.Response(200, json={"result": 600})
        return httpx.Response(200, json={"result": True})

    connector = BitrixConnector(_tenant(), transport=httpx.MockTransport(handler))

    lead_id = asyncio.run(connector.create_lead(_deal()))
    bind = asyncio.run(
        connector.bind_contact_to_lead(
            tenant_id="tenant-a", correlation_id="corr-12345678", lead_id=lead_id, contact_id=321
        )
    )
    attach = asyncio.run(
        connector.attach_document_link(
            lead_id,
            "https://files.example/doc.pdf",
            tenant_id="tenant-a",
            correlation_id="corr-12345678",
        )
    )
    stage = asyncio.run(
        connector.update_stage_with_checklist_block(
            tenant_id="tenant-a",
            correlation_id="corr-12345678",
            lead_id=lead_id,
            stage_id="PRECHECK",
            checklist_block="- doc uploaded",
        )
    )
    manager = asyncio.run(
        connector.manager_verification_required_flag(
            tenant_id="tenant-a", correlation_id="corr-12345678", lead_id=lead_id, required=True
        )
    )
    asyncio.run(connector.aclose())

    assert lead_id == 600
    assert bind is True
    assert attach is True
    assert stage is True
    assert manager is True
    assert all(corr == "corr-12345678" for _, corr, _ in calls)
