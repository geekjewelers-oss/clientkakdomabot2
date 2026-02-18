import logging

try:
    import structlog
except ModuleNotFoundError:  # pragma: no cover
    structlog = None


def configure_logging() -> None:
    if structlog is None:
        logging.basicConfig(level=logging.INFO)
        return
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
    )


def mask_sensitive(value: str | None) -> str | None:
    if value is None:
        return None
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}***{value[-2:]}"
