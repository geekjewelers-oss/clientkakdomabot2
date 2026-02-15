import logging
from typing import Any

import config

logger = logging.getLogger(__name__)


_PROM_COUNTERS: dict[str, Any] = {}
_PROM_GAUGES: dict[str, Any] = {}
_STATSD_CLIENT: Any = None
_STATSD_INIT_DONE = False

_METRIC_NAME_MAP = {
    "ocr.sla.soft_fail": "ocr_sla_soft_fail_total",
    "ocr.sla.auto_accept": "ocr_sla_auto_accept_total",
    "ocr.sla.breach": "ocr_sla_breach_total",
    "ocr.sla.fallback_used": "ocr_sla_fallback_total",
}


def _sanitize_metric_name(name: str) -> str:
    return _METRIC_NAME_MAP.get(name, name.replace(".", "_"))


def _init_statsd() -> Any:
    global _STATSD_CLIENT, _STATSD_INIT_DONE
    if _STATSD_INIT_DONE:
        return _STATSD_CLIENT

    _STATSD_INIT_DONE = True
    try:
        from statsd import StatsClient

        _STATSD_CLIENT = StatsClient()
    except Exception as exc:
        logger.warning("[METRICS] statsd init failed: %s", exc)
        _STATSD_CLIENT = None
    return _STATSD_CLIENT


def inc(name: str, value: int = 1) -> None:
    if not config.OCR_LOG_METRICS_ENABLED:
        return

    backend = (config.OCR_METRICS_BACKEND or "noop").strip().lower()
    if backend == "prometheus":
        prom_name = _sanitize_metric_name(name)
        try:
            from prometheus_client import Counter

            counter = _PROM_COUNTERS.get(prom_name)
            if counter is None:
                counter = Counter(prom_name, f"Counter for {name}")
                _PROM_COUNTERS[prom_name] = counter
            counter.inc(value)
        except Exception as exc:
            logger.warning("[METRICS] prometheus inc failed for %s: %s", name, exc)
        return

    if backend == "statsd":
        client = _init_statsd()
        if client is None:
            return
        try:
            client.incr(name, value)
        except Exception as exc:
            logger.warning("[METRICS] statsd inc failed for %s: %s", name, exc)


def gauge(name: str, value: float) -> None:
    if not config.OCR_LOG_METRICS_ENABLED:
        return

    backend = (config.OCR_METRICS_BACKEND or "noop").strip().lower()
    if backend == "prometheus":
        gauge_name = _sanitize_metric_name(name)
        try:
            from prometheus_client import Gauge

            metric = _PROM_GAUGES.get(gauge_name)
            if metric is None:
                metric = Gauge(gauge_name, f"Gauge for {name}")
                _PROM_GAUGES[gauge_name] = metric
            metric.set(value)
        except Exception as exc:
            logger.warning("[METRICS] prometheus gauge failed for %s: %s", name, exc)
        return

    if backend == "statsd":
        client = _init_statsd()
        if client is None:
            return
        try:
            client.gauge(name, value)
        except Exception as exc:
            logger.warning("[METRICS] statsd gauge failed for %s: %s", name, exc)
