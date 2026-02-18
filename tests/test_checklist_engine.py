from __future__ import annotations

import asyncio
from datetime import date, timedelta

import structlog

from checklist_engine.audit import AuditLogger, InMemoryAuditSink
from checklist_engine.crm_blocker import ChecklistCRMBlocker
from checklist_engine.engine import ChecklistEngine, ChecklistEngineSettings
from checklist_engine.exceptions import ConflictingDocumentsError, DuplicateDocumentError
from checklist_engine.models import DocumentSource, MultiResidentDeal, OverrideRequest, ResidentDocument, ResidentProfile
from checklist_engine.multi_passport import MultiPassportEngine, MultiPassportPolicy
from checklist_engine.rules import NationalityRuleRegistry, StaticRule, build_default_registry


def _doc(
    *,
    resident_id: str = "res-1",
    doc_type: str = "national_passport",
    country_code: str = "RU",
    passport_hash: str = "ph-1",
    mrz_hash: str = "mh-1",
    confidence: float = 0.95,
    verified_flag: bool = False,
    expiry_date_value: str | None = None,
) -> ResidentDocument:
    extracted_fields = {"mrz_checksum_valid": True}
    if expiry_date_value is not None:
        extracted_fields["expiry_date"] = expiry_date_value
    return ResidentDocument(
        resident_id=resident_id,
        doc_type=doc_type,
        country_code=country_code,
        document_url="https://example.com/doc.png",
        passport_hash=passport_hash,
        mrz_hash=mrz_hash,
        extracted_fields=extracted_fields,
        ocr_confidence=confidence,
        verified_flag=verified_flag,
        source=DocumentSource.OCR,
    )


def _engine() -> ChecklistEngine:
    sink = InMemoryAuditSink(records={})
    audit = AuditLogger(sink=sink, logger=structlog.get_logger("test_checklist_audit"))
    return ChecklistEngine(rules_registry=build_default_registry(), settings=ChecklistEngineSettings(), audit_logger=audit)


def test_nationality_rule_mapping_and_required_docs_build() -> None:
    registry = build_default_registry()
    assert registry.resolve_required_docs("RU") == ["national_passport", "residency_form"]
    assert registry.resolve_required_docs("DE") == ["national_id_card", "residency_form"]
    assert registry.resolve_required_docs("IN") == ["foreign_passport", "visa", "entry_stamp"]


def test_registry_extensible_override() -> None:
    registry = NationalityRuleRegistry()
    registry.register_rule("foreign_passport", StaticRule(["foreign_passport"]))
    registry.register_rule("cis_passport", StaticRule(["custom_cis_doc"]))
    registry.register_rule("visa_required", StaticRule(["special_visa_doc"]))
    registry.register_rule("id_card", StaticRule(["national_id_card"]))
    registry.register_fallback(StaticRule(["fallback_doc"]))
    assert registry.resolve_required_docs("RU") == ["custom_cis_doc"]


def test_missing_doc_blocked() -> None:
    engine = _engine()
    checklist = engine.build_checklist(ResidentProfile(resident_id="res-1", nationality="RU"))
    result = engine.evaluate_checklist(resident_docs=[_doc()], checklist=checklist)
    assert result.all_required_satisfied is False
    assert {item.code for item in result.blocking_items} == {"doc::residency_form"}


def test_expired_doc_blocked() -> None:
    engine = _engine()
    checklist = [
        engine.build_checklist(ResidentProfile(resident_id="res-1", nationality="RU"))[0],
    ]
    expired = (date.today() - timedelta(days=1)).isoformat()
    result = engine.evaluate_checklist(
        resident_docs=[_doc(doc_type="national_passport", expiry_date_value=expired)],
        checklist=checklist,
    )
    assert result.all_required_satisfied is False
    assert result.blocking_items


def test_low_confidence_blocked() -> None:
    engine = _engine()
    checklist = [engine.build_checklist(ResidentProfile(resident_id="res-1", nationality="RU"))[0]]
    result = engine.evaluate_checklist(resident_docs=[_doc(confidence=0.20)], checklist=checklist)
    assert result.all_required_satisfied is False
    assert result.blocking_items[0].blocking is True


def test_manual_override_pass() -> None:
    engine = _engine()
    checklist = [engine.build_checklist(ResidentProfile(resident_id="res-1", nationality="RU"))[0]]
    result = engine.evaluate_checklist(
        resident_docs=[_doc(confidence=0.20)],
        checklist=checklist,
        override_request=OverrideRequest(manager_role="supervisor", override_reason="manual verification complete"),
    )
    assert result.all_required_satisfied is True
    assert result.manager_override_used is True


def test_duplicate_passport_blocked() -> None:
    engine = _engine()
    checklist = [engine.build_checklist(ResidentProfile(resident_id="res-1", nationality="RU"))[0]]
    docs = [
        _doc(doc_type="national_passport", passport_hash="dup"),
        _doc(doc_type="residency_form", passport_hash="dup"),
    ]
    try:
        engine.evaluate_checklist(resident_docs=docs, checklist=checklist)
        assert False, "DuplicateDocumentError must be raised"
    except DuplicateDocumentError:
        assert True


def test_multi_resident_bundle_pass() -> None:
    multi = MultiPassportEngine(
        policy=MultiPassportPolicy(
            primary_doc_types={"national_passport", "foreign_passport", "national_id_card"},
            secondary_doc_types={"visa", "migration_card", "residency_form", "entry_stamp"},
        )
    )
    docs = [
        _doc(resident_id="res-1", doc_type="national_passport", passport_hash="hash1", mrz_hash="mrz1", country_code="RU"),
        _doc(resident_id="res-2", doc_type="foreign_passport", passport_hash="hash2", mrz_hash="mrz2", country_code="DE"),
    ]
    grouped = multi.validate_bundle(docs)
    assert set(grouped.keys()) == {"res-1", "res-2"}


def test_conflicting_nationality_fail() -> None:
    multi = MultiPassportEngine(
        policy=MultiPassportPolicy(
            primary_doc_types={"national_passport", "foreign_passport", "national_id_card"},
            secondary_doc_types={"visa", "migration_card", "residency_form", "entry_stamp"},
        )
    )
    docs = [
        _doc(doc_type="national_passport", country_code="RU", passport_hash="hash1", mrz_hash="mrz1"),
        _doc(doc_type="foreign_passport", country_code="KZ", passport_hash="hash1", mrz_hash="mrz1"),
    ]
    try:
        multi.validate_bundle(docs)
        assert False, "ConflictingDocumentsError must be raised"
    except ConflictingDocumentsError:
        assert True


def test_crm_blocker_called() -> None:
    calls: list[tuple[str, bool | str]] = []

    class StubConnector:
        async def update_stage_with_checklist_block(self, **kwargs):
            calls.append(("stage", kwargs["stage_id"]))
            return True

        async def manager_verification_required_flag(self, **kwargs):
            calls.append(("flag", kwargs["required"]))
            return True

        async def attach_document_link(self, *_args, **_kwargs):
            calls.append(("snapshot", True))
            return True

    engine = _engine()
    checklist = engine.build_checklist(ResidentProfile(resident_id="res-1", nationality="RU"))
    result = engine.evaluate_checklist(
        resident_docs=[_doc(doc_type="national_passport"), _doc(doc_type="residency_form", passport_hash="ph-2")],
        checklist=checklist,
    )
    blocker = ChecklistCRMBlocker(connector=StubConnector(), blocked_stage_id="BLOCKED", unblocked_stage_id="READY")
    asyncio.run(engine.enforce_crm_stage(blocker=blocker, tenant_id="tenant", correlation_id="corr", lead_id=10, result=result))
    assert calls[0][0] == "snapshot"
    assert ("stage", "READY") in calls
    assert ("flag", False) in calls


def test_audit_record_created_and_fsm_contract() -> None:
    sink = InMemoryAuditSink(records={})
    engine = ChecklistEngine(
        rules_registry=build_default_registry(),
        settings=ChecklistEngineSettings(),
        audit_logger=AuditLogger(sink=sink, logger=structlog.get_logger("audit_test")),
    )
    payload = engine.evaluate_for_fsm(
        correlation_id="corr-1",
        resident_profile=ResidentProfile(resident_id="res-1", nationality="RU"),
        resident_docs=[
            _doc(doc_type="national_passport", passport_hash="AAA"),
            _doc(doc_type="residency_form", passport_hash="BBB"),
        ],
    )
    assert payload["status"] == "OK"
    assert payload["audit_id"] in sink.records


def test_multi_resident_deal_missing_bundle_fails() -> None:
    multi = MultiPassportEngine(
        policy=MultiPassportPolicy(
            primary_doc_types={"national_passport", "foreign_passport", "national_id_card"},
            secondary_doc_types={"visa", "migration_card", "residency_form", "entry_stamp"},
        )
    )
    deal = MultiResidentDeal(
        deal_id="deal-1",
        residents=[ResidentProfile(resident_id="res-1", nationality="RU"), ResidentProfile(resident_id="res-2", nationality="DE")],
        checklist_per_resident={},
        global_blocking_flag=False,
    )
    try:
        multi.evaluate_multi_resident_deal(deal, [_doc(resident_id="res-1")])
        assert False, "ConflictingDocumentsError must be raised"
    except ConflictingDocumentsError:
        assert True
