from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from .exceptions import NationalityRuleMissing


class DocumentRule(Protocol):
    """Contract for nationality-driven required document generation."""

    def build_required_docs(self, nationality: str) -> list[str]:
        ...


@dataclass(frozen=True)
class StaticRule:
    """Simple rule that always returns the same required document list."""

    required_docs: list[str]

    def build_required_docs(self, nationality: str) -> list[str]:
        _ = nationality
        return list(self.required_docs)


@dataclass
class NationalityRuleRegistry:
    """Configurable and extensible nationality document rule registry."""

    cis_countries: set[str] = field(default_factory=lambda: {"KZ", "RU", "BY", "KG", "UZ", "TJ", "AM", "AZ", "MD"})
    visa_required_countries: set[str] = field(default_factory=lambda: {"IN", "PK", "NG", "EG"})
    id_card_countries: set[str] = field(default_factory=lambda: {"DE", "FR", "ES", "IT", "PL"})
    _named_rules: dict[str, DocumentRule] = field(default_factory=dict)
    _fallback_rule: DocumentRule | None = None

    def register_rule(self, name: str, rule: DocumentRule) -> None:
        self._named_rules[name] = rule

    def register_fallback(self, rule: DocumentRule) -> None:
        self._fallback_rule = rule

    def resolve_required_docs(self, nationality: str) -> list[str]:
        nat = nationality.strip().upper()
        if nat in self.cis_countries:
            return self._require_rule("cis_passport").build_required_docs(nat)
        if nat in self.id_card_countries:
            return self._require_rule("id_card").build_required_docs(nat)
        if nat in self.visa_required_countries:
            return self._require_rule("visa_required").build_required_docs(nat)
        if len(nat) in {2, 3}:
            return self._require_rule("foreign_passport").build_required_docs(nat)
        if self._fallback_rule is not None:
            return self._fallback_rule.build_required_docs(nat)
        raise NationalityRuleMissing(f"Missing nationality rule for {nationality}")

    def _require_rule(self, name: str) -> DocumentRule:
        if name not in self._named_rules:
            raise NationalityRuleMissing(f"Rule '{name}' is not registered")
        return self._named_rules[name]


def build_default_registry() -> NationalityRuleRegistry:
    registry = NationalityRuleRegistry()
    registry.register_rule("cis_passport", StaticRule(["national_passport", "residency_form"]))
    registry.register_rule("foreign_passport", StaticRule(["foreign_passport", "migration_card"]))
    registry.register_rule("visa_required", StaticRule(["foreign_passport", "visa", "entry_stamp"]))
    registry.register_rule("id_card", StaticRule(["national_id_card", "residency_form"]))
    registry.register_fallback(StaticRule(["foreign_passport"]))
    return registry
