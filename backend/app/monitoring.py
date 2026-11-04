"""Optional error reports to Sentry. A knowledge base is private: only what locates a bug leaves it."""

from __future__ import annotations

import logging
from typing import Any

import sentry_sdk

from .extractors.base import ExtractionError

log = logging.getLogger(__name__)

# Expected failures: a page that can't be read is reported on its card, not a bug.
IGNORED = [ExtractionError]
TEXT_LIMIT = 300


def _clip(text: Any) -> Any:
    return text[:TEXT_LIMIT] if isinstance(text, str) else text


def scrub(event: dict[str, Any], _hint: dict[str, Any] | None = None) -> dict[str, Any]:
    """Keeps the error's type, its short text and the stack; drops whatever can carry saved content."""
    for key in ("request", "extra", "user", "breadcrumbs", "modules"):
        event.pop(key, None)
    entry = event.get("logentry")
    if isinstance(entry, dict):
        entry.pop("params", None)
        if "message" in entry:
            entry["message"] = _clip(entry["message"])
    if "message" in event:
        event["message"] = _clip(event["message"])
    for exception in (event.get("exception") or {}).get("values") or []:
        exception["value"] = _clip(exception.get("value"))
        for frame in (exception.get("stacktrace") or {}).get("frames") or []:
            frame.pop("vars", None)
    return event


def init(dsn: str, dev_copy: bool = False, **options: Any) -> bool:
    """Starts the reports when a DSN is set, and says whether it did."""
    if not dsn:
        return False
    sentry_sdk.init(
        dsn=dsn,
        environment="dev" if dev_copy else "production",
        include_local_variables=False,
        max_breadcrumbs=0,
        traces_sample_rate=0,
        ignore_errors=IGNORED,
        before_send=scrub,
        **options,
    )
    log.info("Sentry reports on (%s)", "dev" if dev_copy else "production")
    return True
