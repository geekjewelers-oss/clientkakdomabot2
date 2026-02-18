class ChecklistBlockingError(Exception):
    """Raised when checklist contains blocking validation items."""


class NationalityRuleMissing(Exception):
    """Raised when no nationality rule can be resolved."""


class ConflictingDocumentsError(Exception):
    """Raised when submitted documents conflict by resident identity."""


class DuplicateDocumentError(Exception):
    """Raised when duplicate hashes indicate duplicate documents."""
