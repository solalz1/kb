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


def test_haiku_5_5_is_priced_by_prompt_length():
    short = {"input_tokens": 100_000, "output_tokens": 1_000}
    assert costs.claude_cost("claude-haiku-5-5", short) == pytest.approx(100_000 * 0.1 / 1e6 + 1_000 * 0.5 / 1e6)
    long = {"input_tokens": 100_001, "output_tokens": 1_000}            # past 100 K: the whole request at the long price
    assert costs.claude_cost("claude-haiku-5-5", long) == pytest.approx(100_001 * 0.5 / 1e6 + 1_000 * 2.5 / 1e6)
    cached = {"input_tokens": 50_000, "cache_read_input_tokens": 60_000, "output_tokens": 0}   # cache counts in the prompt
    assert costs.claude_cost("claude-haiku-5-5", cached) == pytest.approx(50_000 * 0.5 / 1e6 + 60_000 * 0.05 / 1e6)
    assert costs.claude_cost("claude-haiku-4-5", long) == pytest.approx(100_001 * 1 / 1e6 + 1_000 * 5 / 1e6)


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


def _by_service(client):
    return {s["id"]: s for s in client.get("/api/costs", headers=AUTH).json()["services"]}


def test_claude_costs_come_from_the_console_with_an_admin_key(client, billing_apis, monkeypatch):
    from app import db
    from app.config import get_settings

    now = datetime.now(timezone.utc)
    last_month = (now.replace(day=1) - timedelta(days=3)).replace(hour=9)
    db.execute("insert into items (kind, title, status, created_at) values ('note', 'La première', 'ready', %s)",
               (last_month,))
    costs.record("anthropic", 0.12)

    # without an admin key: the KB's own measure, marked as an estimate
    by = _by_service(client)
    assert by["anthropic"]["synced"] is False and by["anthropic"]["month"] == 0.12
    assert billing_apis.calls == [(costs.X_CREDITS_URL, billing_apis.calls[0][1], {})]   # only X was asked

    # the Console bills in cents, by day, a page at a time
    monkeypatch.setattr(get_settings(), "anthropic_admin_key", "sk-ant-admin01-test")
    day = lambda d, *cents: {"starting_at": d.strftime("%Y-%m-%dT00:00:00Z"),            # noqa: E731
                             "results": [{"amount": c, "currency": "USD"} for c in cents]}
    pages = {None: {"data": [day(last_month, "250.5")], "has_more": True, "next_page": "p2"},
             "p2": {"data": [day(now, "40", "17.25"), day(now + timedelta(days=1))], "has_more": False, "next_page": None}}
    billing_apis.answers[costs.ANTHROPIC_COST_URL] = lambda params: pages[params.get("page")]
    costs._cache.clear()
    billing_apis.calls.clear()

    claude = _by_service(client)["anthropic"]
    assert claude["synced"] is True and claude["sync_error"] is None
    assert claude["month"] == pytest.approx(0.5725) and claude["total"] == pytest.approx(3.0775)
    assert claude["kb_month"] == 0.12                                   # what the KB itself used, shown alongside
    console = [c for c in billing_apis.calls if c[0] == costs.ANTHROPIC_COST_URL]
    assert len(console) == 2 and console[0][1]["x-api-key"] == "sk-ant-admin01-test"
    assert console[0][2]["starting_at"] == last_month.strftime("%Y-%m-%dT00:00:00Z")
    assert console[0][2]["bucket_width"] == "1d" and console[1][2]["page"] == "p2"

    # the monthly spend limit set in the Console; "before tracking" doesn't apply to the Console's own figure
    r = client.put("/api/costs/anthropic", json={"limit": 20, "before": 7.5}, headers=AUTH)
    claude = {s["id"]: s for s in r.json()["services"]}["anthropic"]
    assert claude["limit"] == 20 and claude["left_this_month"] == pytest.approx(19.4275)
    assert claude["total"] == pytest.approx(3.0775)
    assert len([c for c in billing_apis.calls if c[0] == costs.ANTHROPIC_COST_URL]) == 2    # cached for a while

    # the Console doesn't answer: back to the estimate, with the reason
    billing_apis.answers[costs.ANTHROPIC_COST_URL] = lambda params: (_ for _ in ()).throw(
        RuntimeError("401 : invalid x-api-key"))
    costs._cache.clear()
    claude = _by_service(client)["anthropic"]
    assert claude["synced"] is False and "401" in claude["sync_error"]
    assert claude["month"] == 0.12 and claude["total"] == pytest.approx(0.12 + 7.5)
    assert claude["left_this_month"] == pytest.approx(19.88)

    assert client.put("/api/costs/anthropic", json={"limit": 0}, headers=AUTH).json()["services"][0]["limit"] is None
    assert client.put("/api/costs/anthropic", json={"limit": -1}, headers=AUTH).status_code == 400


def test_x_balance_comes_from_x(client, billing_apis):
    costs.record("x", 0.05)
    client.put("/api/costs/x", json={"balance": 10}, headers=AUTH)

    # X doesn't answer: the balance noted by hand, minus what the KB read since
    x = _by_service(client)["x"]
    assert x["remaining"] == 10 and x["remaining_synced"] is False and x["sync_error"]

    billing_apis.answers[costs.X_CREDITS_URL] = {"data": {"total_balance": 4.2, "prepaid_balance": 4.2,
                                                          "free_balance": 0, "free_grants": []}}
    costs._cache.clear()
    x = _by_service(client)["x"]
    assert x["remaining"] == 4.2 and x["remaining_synced"] is True and x["sync_error"] is None
    assert x["month"] == 0.05 and x["synced"] is False                  # what was spent stays the KB's measure
    token = [c[1] for c in billing_apis.calls if c[0] == costs.X_CREDITS_URL][-1]["Authorization"]
    assert token == "Bearer x-test"


def test_voyage_bills_only_past_its_free_tokens(client, monkeypatch):
    from app import db
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "embeddings_provider", "voyage")
    last_month = datetime.now(timezone.utc).replace(day=1) - timedelta(days=2)
    db.execute("""insert into usage_log (service, model, cost, units, at)
                  values ('voyage', 'voyage-4', 9, '{"tokens": 150000000}', %s)""", (last_month,))
    voyage = _by_service(client)["voyage"]
    assert voyage["month"] == 0 and voyage["total"] == 0 and voyage["tokens"] == 150_000_000

    costs.record("voyage", 6, model="voyage-4", units={"tokens": 100_000_000})
    voyage = _by_service(client)["voyage"]
    assert voyage["tokens"] == 250_000_000
    assert voyage["month"] == pytest.approx(3.0) and voyage["total"] == pytest.approx(3.0)   # 50 M past the free 200 M
