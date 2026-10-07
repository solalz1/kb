"""What the KB costs: every paid call is measured and priced, and the user adds balances and plans."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app import costs

from .test_e2e import AUTH


@pytest.fixture
def client(clean_db, fake_llm):
    from app.main import app

    with TestClient(app) as c:
        yield c


def _log():
    from app import db

    return db.fetchall("select service, model, cost::float cost, units, purpose from usage_log order by id")


@pytest.mark.parametrize("model, expected", [
    ("claude-haiku-4-5", 1 * 1 + 0.5 * 5),             # 1 M input at $1, 0.5 M output at $5
    ("claude-sonnet-5-5", 1 * 2 + 0.5 * 10),
    ("claude-opus-5-5", 1 * 4 + 0.5 * 20),
    ("claude-fable-5-1", 1 * 10 + 0.5 * 50),
    ("claude-opus-4-1-20250805", 1 * 15 + 0.5 * 75),
    ("claude-opus-4-6", 1 * 5 + 0.5 * 25),
])
def test_claude_prices(model, expected):
    assert costs.claude_cost(model, {"input_tokens": 1_000_000, "output_tokens": 500_000}) == pytest.approx(expected)


def test_cache_tokens_are_priced():
    usage = SimpleNamespace(input_tokens=0, output_tokens=0, cache_read_input_tokens=1_000_000,
                            cache_creation_input_tokens=1_000_000, cache_creation=None)
    assert costs.claude_cost("claude-haiku-4-5", usage) == pytest.approx(0.1 + 1.25)
    usage.cache_creation = SimpleNamespace(ephemeral_5m_input_tokens=0, ephemeral_1h_input_tokens=1_000_000)
    assert costs.claude_cost("claude-opus-5-5", usage) == pytest.approx(0.2 + 8)


def test_every_paid_call_is_measured(clean_db, monkeypatch):
    from app import embeddings, llm, media
    from app.config import get_settings
    from app.extractors.twitter import XClient

    # Claude: a forced tool call, with its usage
    usage = SimpleNamespace(input_tokens=2000, output_tokens=500, cache_read_input_tokens=0, cache_creation_input_tokens=0)
    resp = SimpleNamespace(content=[SimpleNamespace(type="tool_use", input={"ok": True})], stop_reason="tool_use", usage=usage)
    monkeypatch.setattr(llm, "client", lambda: SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: resp)))
    monkeypatch.setattr(llm, "_NO_FORCED_TOOL", set())
    llm.call_tool(system="s", content="c", tool_name="enrich_item", tool_description="d", schema={"type": "object"},
                  model="claude-haiku-4-5")

    # Voyage: the tokens it reports
    s = get_settings()
    monkeypatch.setattr(s, "voyage_api_key", "v")
    monkeypatch.setattr(embeddings.httpx, "post", lambda *a, **k: httpx.Response(
        200, json={"data": [{"index": 0, "embedding": [0.1]}], "usage": {"total_tokens": 1_000_000}},
        request=httpx.Request("POST", "https://api.voyageai.com/v1/embeddings")))
    embeddings._voyage(["un texte"], "document")

    # transcription: the seconds of audio, 10 s at least
    monkeypatch.setattr(media, "_post_with_retry", lambda *a, **k: httpx.Response(
        200, json={"text": "bonjour", "duration": 3}, request=httpx.Request("POST", "https://api.groq.com")))
    media._transcribe_segment(media.Path("/nonexistent.mp3"), 0)

    # X: posts returned, referenced ones included
    XClient("t")._absorb({"data": [{"id": "1"}, {"id": "2"}], "includes": {"tweets": [{"id": "3"}]}})

    log = _log()
    assert [r["service"] for r in log] == ["anthropic", "voyage", "transcription", "x"]
    assert log[0]["cost"] == pytest.approx((2000 * 1 + 500 * 5) / 1e6) and log[0]["purpose"] == "enrich_item"
    assert log[0]["units"]["input_tokens"] == 2000
    assert log[1]["cost"] == pytest.approx(0.06) and log[1]["units"] == {"tokens": 1_000_000}
    assert log[2]["cost"] == pytest.approx(10 / 3600 * 0.04, abs=1e-8)         # stored to 1e-8 $
    assert log[3]["cost"] == pytest.approx(3 * 0.005)


def test_measuring_never_breaks_the_call(clean_db, monkeypatch):
    from app import db

    monkeypatch.setattr(db, "execute", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("base indisponible")))
    costs.record("anthropic", 1.0)                      # logged, not raised


def test_summary_balances_plans_and_totals(client):
    from app import db

    now = datetime.now(timezone.utc)
    two_months_ago = (now.replace(day=1) - timedelta(days=40)).replace(day=15)
    db.execute("insert into items (kind, title, status, created_at) values ('note', 'La première', 'ready', %s)",
               (two_months_ago,))
    db.execute("insert into usage_log (service, cost, at) values ('anthropic', 2.5, %s)", (two_months_ago,))
    costs.record("anthropic", 1.25)
    costs.record("x", 0.05)

    body = client.get("/api/costs", headers=AUTH).json()
    by = {s["id"]: s for s in body["services"]}
    assert by["anthropic"]["month"] == 1.25 and by["anthropic"]["total"] == 3.75
    assert by["x"]["month"] == 0.05
    assert by["railway"]["monthly"] == 5.0 and by["railway"]["month"] == 5.0 and by["railway"]["total"] == 15.0  # 3 months
    assert "notion" not in by                           # not configured, nothing spent: not shown
    assert body["month"] == pytest.approx(1.25 + 0.05 + 5.0)
    assert body["total"] == pytest.approx(3.75 + 0.05 + 15.0)

    # a balance read in the console: what the KB spends afterwards comes off it
    r = client.put("/api/costs/anthropic", json={"balance": 20}, headers=AUTH)
    assert {s["id"]: s for s in r.json()["services"]}["anthropic"]["remaining"] == 20
    costs.record("anthropic", 0.4)
    by = {s["id"]: s for s in client.get("/api/costs", headers=AUTH).json()["services"]}
    assert by["anthropic"]["remaining"] == 19.6

    # spent before measuring started, and a different plan
    client.put("/api/costs/anthropic", json={"before": 7.5}, headers=AUTH)
    client.put("/api/costs/railway", json={"monthly": 8}, headers=AUTH)
    body = client.get("/api/costs", headers=AUTH).json()
    by = {s["id"]: s for s in body["services"]}
    assert by["anthropic"]["total"] == pytest.approx(3.75 + 0.4 + 7.5) and by["anthropic"]["remaining"] == 19.6
    assert by["railway"]["total"] == 24.0

    assert client.put("/api/costs/anthropic", json={"clear_balance": True}, headers=AUTH).json()["services"][0]["remaining"] is None
    assert client.put("/api/costs/inconnu", json={"monthly": 1}, headers=AUTH).status_code == 404
    assert client.put("/api/costs/railway", json={"monthly": -1}, headers=AUTH).status_code == 400
