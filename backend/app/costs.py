"""What the KB costs, service by service.

Every paid call is measured where it happens (Claude tokens, Voyage tokens, seconds of audio, X reads) and priced with
the public price lists below; nothing here calls a billing API, since none of these services tells a key its balance.
The user adds what only they know: the balance they see in a console (the app subtracts what the KB spent since),
what was spent before measuring started, and fixed monthly plans (Railway, Supabase).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from . import db
from .config import get_settings

log = logging.getLogger(__name__)

SETTING = "costs"

# $ per million tokens: base input, 5-minute cache write, 1-hour cache write, cache read, output
# (platform.claude.com/docs/en/about-claude/pricing). First matching prefix wins: specific ids first.
CLAUDE_PRICES: list[tuple[str, tuple[float, float, float, float, float]]] = [
    ("claude-fable-5-1", (10, 12.5, 20, 0.25, 50)),
    ("claude-mythos-5-1", (10, 12.5, 20, 0.25, 50)),
    ("claude-fable-5", (10, 12.5, 20, 1, 50)),
    ("claude-mythos-5", (10, 12.5, 20, 1, 50)),
    ("claude-opus-5-5", (4, 5, 8, 0.2, 20)),
    ("claude-opus-5", (5, 6.25, 10, 0.5, 25)),
    ("claude-opus-4-1", (15, 18.75, 30, 1.5, 75)),
    ("claude-opus-4-2", (15, 18.75, 30, 1.5, 75)),      # claude-opus-4-20250514
    ("claude-opus-4", (5, 6.25, 10, 0.5, 25)),          # Opus 4.5 to 4.8
    ("claude-sonnet-5", (2, 2.5, 4, 0.2, 10)),          # Sonnet 5 and 5.5
    ("claude-sonnet-4", (3, 3.75, 6, 0.3, 15)),
    ("claude-haiku-4", (1, 1.25, 2, 0.1, 5)),
    ("claude-3-5-haiku", (0.8, 1, 1.6, 0.08, 4)),
]
CLAUDE_UNKNOWN = (3, 3.75, 6, 0.3, 15)

VOYAGE_PRICES = {"voyage-4": 0.06, "voyage-4-lite": 0.02, "voyage-4-large": 0.12, "voyage-3.5": 0.06,
                 "voyage-3.5-lite": 0.02}           # $ per million tokens; the first 200 M tokens of voyage-4 are free
TRANSCRIPTION_PRICES = {"whisper-large-v3-turbo": 0.04, "whisper-large-v3": 0.111, "whisper-1": 0.36}  # $ per hour
TRANSCRIPTION_MIN_SECONDS = 10                      # Groq bills at least 10 s per request
X_POST_READ = 0.005                                 # X API pay-per-use, per post returned
X_USER_READ = 0.01                                  # per account returned (lookups, following lists)

# The services a KB uses. kind: "metered" (measured here), "plan" (a fixed monthly price the user enters).
SERVICES: list[dict[str, Any]] = [
    {"id": "anthropic", "name": "Claude (Anthropic)", "kind": "metered", "prepaid": True,
     "url": "https://platform.claude.com/settings/billing"},
    {"id": "voyage", "name": "Voyage AI", "kind": "metered", "prepaid": False, "url": "https://dashboard.voyageai.com/",
     "free": "200 M tokens"},
    {"id": "x", "name": "X API", "kind": "metered", "prepaid": True, "url": "https://console.x.com/"},
    {"id": "transcription", "name": "Groq", "kind": "metered", "prepaid": False, "url": "https://console.groq.com/settings/billing"},
    {"id": "railway", "name": "Railway", "kind": "plan", "monthly": 5.0, "url": "https://railway.com/dashboard"},
    {"id": "supabase", "name": "Supabase", "kind": "plan", "monthly": 0.0, "url": "https://supabase.com/dashboard"},
    {"id": "notion", "name": "Notion", "kind": "plan", "monthly": 0.0, "url": "https://www.notion.so/"},
]
SERVICE_IDS = [s["id"] for s in SERVICES]


# ---------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------

def claude_prices(model: str) -> tuple[float, float, float, float, float]:
    for prefix, prices in CLAUDE_PRICES:
        if model.startswith(prefix):
            return prices
    return CLAUDE_UNKNOWN


def _get(obj: Any, key: str) -> Any:
    return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)


def claude_cost(model: str, usage: Any) -> float:
    """Price of one response from its `usage` (an SDK object or a dict). Thinking is billed as output."""
    base, write5, write1h, read, out = claude_prices(model)
    creation = _get(usage, "cache_creation")
    w5 = _get(creation, "ephemeral_5m_input_tokens") if creation else None
    w1h = _get(creation, "ephemeral_1h_input_tokens") if creation else None
    if w5 is None and w1h is None:
        w5, w1h = _get(usage, "cache_creation_input_tokens") or 0, 0
    total = ((_get(usage, "input_tokens") or 0) * base + (w5 or 0) * write5 + (w1h or 0) * write1h
             + (_get(usage, "cache_read_input_tokens") or 0) * read + (_get(usage, "output_tokens") or 0) * out)
    return total / 1_000_000


def voyage_cost(model: str, tokens: int) -> float:
    return tokens * VOYAGE_PRICES.get(model, 0.06) / 1_000_000


def transcription_cost(model: str, seconds: float) -> float:
    return max(seconds, TRANSCRIPTION_MIN_SECONDS) / 3600 * TRANSCRIPTION_PRICES.get(model, 0.04)


# ---------------------------------------------------------------------------
# Measuring
# ---------------------------------------------------------------------------

def record(service: str, cost: float, *, model: str | None = None, units: dict | None = None,
           purpose: str | None = None) -> None:
    """Writes one paid call. Never raises: measuring must not break what it measures."""
    try:
        db.execute("insert into usage_log (service, model, cost, units, purpose) values (%s, %s, %s, %s, %s)",
                   (service, model, round(cost, 8), db.jsonb(units or {}), purpose))
    except Exception:
        log.warning("Coût non enregistré (%s)", service, exc_info=True)


def record_claude(model: str, usage: Any, purpose: str | None = None) -> None:
    if usage is None:
        return
    units = {k: _get(usage, k) or 0 for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens",
                                                "cache_read_input_tokens")}
    record("anthropic", claude_cost(model, usage), model=model, units=units, purpose=purpose)


def record_x(posts: int = 0, users: int = 0, purpose: str | None = None) -> None:
    if posts or users:
        record("x", posts * X_POST_READ + users * X_USER_READ, units={"posts": posts, "users": users}, purpose=purpose)


# ---------------------------------------------------------------------------
# What the user enters
# ---------------------------------------------------------------------------

def user_settings() -> dict:
    row = db.fetchone("select value from kb_settings where key = %s", (SETTING,))
    return dict(row["value"]) if row else {}


def set_service(service: str, *, balance: float | None = None, before: float | None = None,
                monthly: float | None = None, clear_balance: bool = False) -> dict:
    if service not in SERVICE_IDS:
        raise ValueError(f"Service inconnu : {service}")
    all_settings = user_settings()
    cur = dict(all_settings.get(service) or {})
    if clear_balance:
        cur.pop("balance", None)
        cur.pop("balance_at", None)
    if balance is not None:
        cur["balance"] = round(float(balance), 2)
        cur["balance_at"] = datetime.now(timezone.utc).isoformat()
    if before is not None:
        cur["before"] = round(float(before), 2)
    if monthly is not None:
        cur["monthly"] = round(float(monthly), 2)
    all_settings[service] = cur
    db.execute(
        """insert into kb_settings (key, value) values (%s, %s)
           on conflict (key) do update set value = excluded.value, updated_at = now()""",
        (SETTING, db.jsonb(all_settings)))
    return summary()


# ---------------------------------------------------------------------------
# Summary for the Settings page
# ---------------------------------------------------------------------------

def _months_between(start: datetime, end: datetime) -> int:
    """Calendar months a monthly plan has been paid for, the current one included."""
    return (end.year - start.year) * 12 + end.month - start.month + 1


def _in_use(service: str) -> bool:
    s = get_settings()
    return {
        "anthropic": bool(s.anthropic_api_key),
        "voyage": s.embeddings_provider == "voyage",
        "x": bool(s.x_bearer_token),
        "transcription": bool(s.transcription_api_key),
        "notion": bool(s.notion_token),
    }.get(service, True)


def summary(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    rows = db.fetchall(
        """select service, coalesce(sum(cost), 0)::float as total,
                  coalesce(sum(cost) filter (where at >= %s), 0)::float as month
           from usage_log group by service""", (month_start,))
    measured = {r["service"]: r for r in rows}
    since = db.fetchone("select min(at) at from usage_log")["at"]
    first_item = db.fetchone("select min(created_at) at from items")["at"]
    started = min([d for d in (first_item, since) if d] or [now])
    user = user_settings()

    services, month_total, all_total = [], 0.0, 0.0
    for svc in SERVICES:
        sid = svc["id"]
        mine = user.get(sid) or {}
        m = measured.get(sid) or {}
        if svc["kind"] == "plan":
            monthly = float(mine.get("monthly", svc["monthly"]))
            month = monthly
            total = monthly * _months_between(started, now)
        else:
            monthly = None
            month = float(m.get("month") or 0)
            total = float(m.get("total") or 0)
        total += float(mine.get("before") or 0)
        if not (_in_use(sid) or month or total or mine):
            continue
        remaining = None
        if mine.get("balance") is not None:
            spent_since = db.fetchone(
                "select coalesce(sum(cost), 0)::float s from usage_log where service = %s and at >= %s",
                (sid, mine["balance_at"]))["s"]
            remaining = round(float(mine["balance"]) - spent_since, 4)
        services.append({
            "id": sid, "name": svc["name"], "kind": svc["kind"], "url": svc["url"], "prepaid": svc.get("prepaid", False),
            "free": svc.get("free"), "in_use": _in_use(sid),
            "month": round(month, 4), "total": round(total, 4), "monthly": monthly,
            "before": mine.get("before"), "balance": mine.get("balance"), "balance_at": mine.get("balance_at"),
            "remaining": remaining,
        })
        month_total += month
        all_total += total
    return {
        "month": round(month_total, 4), "total": round(all_total, 4),
        "measured_since": since.isoformat() if since else None, "started": started.isoformat(),
        "services": services,
    }
