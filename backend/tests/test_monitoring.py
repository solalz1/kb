import sentry_sdk

from app import monitoring
from app.extractors.base import ExtractionError


def test_no_dsn_starts_nothing():
    assert monitoring.init("") is False


def test_scrub_drops_what_can_carry_saved_content():
    event = {
        "request": {"data": "my private note", "headers": {"Authorization": "Bearer x"}},
        "extra": {"content": "my private note"},
        "user": {"ip_address": "127.0.0.1"},
        "breadcrumbs": {"values": [{"message": "my private note"}]},
        "logentry": {"message": "n" * 1000, "params": ["my private note"]},
        "exception": {
            "values": [{
                "type": "ValueError",
                "value": "v" * 1000,
                "stacktrace": {"frames": [{"function": "f", "vars": {"text": "my private note"}}]},
            }]
        },
    }
    out = monitoring.scrub(event)
    assert "my private note" not in str(out)
    assert out["exception"]["values"][0]["type"] == "ValueError"
    assert out["exception"]["values"][0]["stacktrace"]["frames"][0]["function"] == "f"
    assert len(out["exception"]["values"][0]["value"]) == monitoring.TEXT_LIMIT
    assert len(out["logentry"]["message"]) == monitoring.TEXT_LIMIT


def _sent(**options):
    events = []

    class Transport(sentry_sdk.transport.Transport):
        def capture_envelope(self, envelope):
            events.extend(item.payload.json for item in envelope.items if item.headers.get("type") == "event")

    assert monitoring.init("https://key@sentry.example.com/1", transport=Transport, **options) is True
    return events


def test_an_error_is_reported_without_its_local_variables():
    events = _sent()
    try:
        secret_note = "my private note"
        raise ValueError(f"failed after {len(secret_note)} characters")
    except ValueError as error:
        sentry_sdk.capture_exception(error)
    sentry_sdk.flush()

    assert len(events) == 1
    assert events[0]["environment"] == "production"
    assert events[0]["exception"]["values"][0]["type"] == "ValueError"
    assert "my private note" not in str({k: v for k, v in events[0].items() if k != "exception"})
    assert all("vars" not in frame for frame in events[0]["exception"]["values"][0]["stacktrace"]["frames"])


def test_an_expected_extraction_failure_is_not_reported():
    events = _sent()
    sentry_sdk.capture_exception(ExtractionError("page refused"))
    sentry_sdk.flush()
    assert events == []


def test_no_personal_data_is_sent_by_default():
    assert monitoring.init("https://key@sentry.example.com/1") is True
    assert sentry_sdk.get_client().should_send_default_pii() is False


def test_a_dev_copy_is_told_apart():
    events = _sent(dev_copy=True)
    sentry_sdk.capture_message("hello")
    sentry_sdk.flush()
    assert events[0]["environment"] == "dev"
