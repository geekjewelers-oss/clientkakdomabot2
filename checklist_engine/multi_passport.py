from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .exceptions import ConflictingDocumentsError
from .models import MultiResidentDeal, ResidentDocument


@dataclass(frozen=True)
class MultiPassportPolicy:
    primary_doc_types: set[str]
    secondary_doc_types: set[str]


@dataclass
class MultiPassportEngine:
    policy: MultiPassportPolicy

    def group_by_resident(self, documents: list[ResidentDocument]) -> dict[str, list[ResidentDocument]]:
        grouped: dict[str, list[ResidentDocument]] = defaultdict(list)
        for document in documents:
            grouped[document.resident_id].append(document)
        return dict(grouped)

    def validate_bundle(self, documents: list[ResidentDocument]) -> dict[str, list[ResidentDocument]]:
        grouped = self.group_by_resident(documents)
        for resident_id, resident_docs in grouped.items():
            self._validate_resident_documents(resident_id, resident_docs)
        return grouped

    def evaluate_multi_resident_deal(self, deal: MultiResidentDeal, all_documents: list[ResidentDocument]) -> MultiResidentDeal:
        grouped = self.validate_bundle(all_documents)
        missing_residents = [resident.resident_id for resident in deal.residents if resident.resident_id not in grouped]
        if missing_residents:
            raise ConflictingDocumentsError(f"Missing document bundle for residents: {missing_residents}")
        return deal

    def _validate_resident_documents(self, resident_id: str, documents: list[ResidentDocument]) -> None:
        primary_docs = [doc for doc in documents if doc.doc_type in self.policy.primary_doc_types]
        if not primary_docs:
            raise ConflictingDocumentsError(f"Resident {resident_id} missing primary identification")

        passport_hashes = {doc.passport_hash for doc in primary_docs}
        if len(passport_hashes) > 1:
            raise ConflictingDocumentsError(f"Resident {resident_id} has mixed passport hashes")

        nationalities = {doc.country_code for doc in primary_docs}
        if len(nationalities) > 1:
            raise ConflictingDocumentsError(f"Resident {resident_id} has conflicting nationality")

        mrz_hashes = {doc.mrz_hash for doc in primary_docs}
        if len(mrz_hashes) > 1:
            raise ConflictingDocumentsError(f"Resident {resident_id} has conflicting MRZ hashes")

        for doc in documents:
            if doc.doc_type in self.policy.primary_doc_types:
                continue
            if doc.doc_type not in self.policy.secondary_doc_types:
                raise ConflictingDocumentsError(f"Unsupported document type in bundle: {doc.doc_type}")
