"""Structured answers from Claude: a forced tool call, or structured outputs on models that refuse forced tools."""

import io
import json
from types import SimpleNamespace

import anthropic
import httpx
import pytest

REFUSED = 'tool_choice: type "tool" and "any" are not supported for this model.'


def _bad_request(message):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    body = {"type": "error", "error": {"type": "invalid_request_error", "message": message}}
    return anthropic.BadRequestError(f"Error code: 400 - {body}", response=httpx.Response(400, request=request),
                                     body=body)


class FakeClaude:
    """Haiku (4.5 and 5.5) accepts a forced tool; Sonnet 5.5 and Opus 5.5 refuse it, think first, and answer in JSON
    text."""

    def __init__(self, answer, stop_reason=None):
        self.answer = answer
        self.stop_reason = stop_reason
        self.calls = []
        self.messages = SimpleNamespace(create=self._call, stream=self._stream)

    def _call(self, **kw):
        self.calls.append(kw)
        forced = (kw.get("tool_choice") or {}).get("type") in ("tool", "any")
        if not kw["model"].startswith("claude-haiku"):
            if forced:
                raise _bad_request(REFUSED)
            content = [SimpleNamespace(type="thinking", thinking="", signature="sig"),
                       SimpleNamespace(type="text", text=json.dumps(self.answer))]
            return SimpleNamespace(content=content, stop_reason=self.stop_reason or "end_turn")
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input=self.answer)],
                               stop_reason=self.stop_reason or "tool_use")

    def _stream(self, **kw):
        resp = self._call(**kw)

        class Stream:
            def __enter__(self):
                return SimpleNamespace(get_final_message=lambda: resp)

            def __exit__(self, *exc):
                return False

        return Stream()


@pytest.fixture
def claude(monkeypatch, clean_db):
    from app import llm

    monkeypatch.setattr(llm, "_NO_FORCED_TOOL", set())
    fake = FakeClaude({"headline": "Une journée calme.", "entries": [{"id": "hn:1", "title": "T", "summary": "S"}]})
    monkeypatch.setattr(llm, "client", lambda: fake)
    return fake


def _write_digest(model, max_tokens=8000):
    from app import llm
    from app.digest.agent import DAILY_SCHEMA

    return llm.call_tool(system="Tu rédiges le digest.", content="Date : 7 octobre", tool_name="write_digest",
                         tool_description="Enregistre le digest rédigé.", schema=DAILY_SCHEMA, model=model,
                         max_tokens=max_tokens)


def test_forced_tool_when_the_model_accepts_it(claude):
    out = _write_digest("claude-haiku-5-5", max_tokens=2000)
    assert out["headline"] == "Une journée calme."
    assert len(claude.calls) == 1
    assert claude.calls[0]["tool_choice"] == {"type": "tool", "name": "write_digest"}


def test_structured_outputs_when_the_model_refuses_forced_tools(claude):
    from app.digest.agent import DAILY_SCHEMA

    out = _write_digest("claude-sonnet-5-5")
    assert out == {"headline": "Une journée calme.", "entries": [{"id": "hn:1", "title": "T", "summary": "S"}]}
    refused, answered = claude.calls
    assert refused["tool_choice"]["type"] == "tool"
    assert "tool_choice" not in answered and "tools" not in answered
    fmt = answered["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"]["additionalProperties"] is False
    assert fmt["schema"]["properties"]["entries"]["items"]["additionalProperties"] is False
    assert fmt["schema"]["required"] == DAILY_SCHEMA["required"]
    assert answered["max_tokens"] >= 16000           # the thinking counts in max_tokens
    assert "Enregistre le digest rédigé." in answered["system"]

    # the refusal is remembered: the next call goes straight to structured outputs
    _write_digest("claude-sonnet-5-5")
    assert len(claude.calls) == 3 and "output_config" in claude.calls[2]
    # and other models keep their forced tool
    _write_digest("claude-haiku-5-5", max_tokens=2000)
    assert claude.calls[3]["tool_choice"]["type"] == "tool"


def test_other_errors_are_not_hidden(claude, monkeypatch):
    from app import llm

    def broken(**kw):
        raise _bad_request("messages: text content blocks must be non-empty")

    monkeypatch.setattr(claude.messages, "stream", broken)
    with pytest.raises(anthropic.BadRequestError):
        _write_digest("claude-opus-5-5")
    assert "claude-opus-5-5" not in llm._NO_FORCED_TOOL


@pytest.mark.parametrize("stop, message", [("max_tokens", "tronquée"), ("refusal", "refusé")])
def test_cut_or_refused_answers(claude, stop, message):
    claude.stop_reason = stop
    with pytest.raises(RuntimeError, match=message):
        _write_digest("claude-sonnet-5-5")


def test_strict_schema_closes_every_object_and_keeps_the_rest():
    from app import llm
    from app.digest import agent, profile

    schema = {"type": "object", "properties": {
        "name": {"type": "string", "minLength": 2, "maxLength": 90, "description": "Nom."},
        "score": {"type": "integer", "minimum": 1, "maximum": 5, "enum": [1, 2, 3, 4, 5]},
        "tags": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 8},
        "steps": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "minimum": {"type": "object", "properties": {"x": {"type": "number"}}},
    }, "required": ["name"]}
    out = llm.strict_schema(schema)
    assert out == {"type": "object", "properties": {
        "name": {"type": "string", "description": "Nom."},
        "score": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
        "tags": {"type": "array", "items": {"type": "string"}},
        "steps": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "minimum": {"type": "object", "properties": {"x": {"type": "number"}}, "additionalProperties": False},
    }, "required": ["name"], "additionalProperties": False}
    assert "additionalProperties" not in schema              # the original is left alone

    def objects(s):
        if isinstance(s, dict):
            if s.get("type") == "object":
                yield s
            for v in s.values():
                yield from objects(v)
        elif isinstance(s, list):
            for v in s:
                yield from objects(v)

    for real in (agent.PICK_SCHEMA, agent.DAILY_SCHEMA, agent.WEEKLY_SCHEMA, agent.PROJECTS_SCHEMA,
                 profile.PROFILE_SCHEMA, llm.ENRICH_SCHEMA, llm.LINKS_SCHEMA, llm.PLAN_SCHEMA, llm.IMAGE_SCHEMA):
        strict = llm.strict_schema(real)
        assert all(o["additionalProperties"] is False for o in objects(strict))
        assert strict.get("required") == real.get("required")


class PlainClaude:
    """Answers plain text, as create() or as a stream, and keeps every request."""

    def __init__(self):
        self.calls = []
        resp = SimpleNamespace(content=[SimpleNamespace(type="text", text="agents rag")], stop_reason="end_turn", usage=None)
        calls = self.calls

        class Stream:
            def __init__(self, **kw):
                calls.append(kw)
                self.text_stream = iter(["agents ", "rag"])

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def get_final_message(self):
                return resp

        self.messages = SimpleNamespace(create=lambda **kw: (calls.append(kw), resp)[1], stream=Stream)


@pytest.fixture
def plain(monkeypatch, clean_db):
    from app import llm

    fake = PlainClaude()
    monkeypatch.setattr(llm, "client", lambda: fake)
    return fake


def _frame() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_models_think_by_default_with_room_for_it(plain):
    """Haiku 5.5, Sonnet 5.5 and Opus 5.5 think by default and max_tokens counts it: plain calls and chat answers
    leave thinking on and add room for it, so a short answer never runs out of tokens."""
    from app import llm
    from app.config import get_settings

    assert get_settings().enrich_model == "claude-haiku-5-5" and llm.thinking_enabled() is True
    assert llm.complete(system="s", prompt="p", max_tokens=200) == "agents rag"
    assert "thinking" not in plain.calls[-1] and plain.calls[-1]["max_tokens"] == 200 + llm.THINKING_ROOM
    assert llm.describe_frames([_frame()]) == "agents rag"
    assert "thinking" not in plain.calls[-1] and plain.calls[-1]["max_tokens"] == 2000 + llm.THINKING_ROOM
    assert llm.transcribe_pdf(b"%PDF-1.4", first_page=11) == "agents rag"
    assert "thinking" not in plain.calls[-1] and plain.calls[-1]["max_tokens"] == 20000 + llm.THINKING_ROOM

    for model, room in (("claude-haiku-5-5", llm.THINKING_ROOM), ("claude-sonnet-5-5", llm.THINKING_ROOM),
                        ("claude-opus-5-5", llm.THINKING_ROOM), ("claude-haiku-4-5", 0)):
        out = "".join(llm.stream_text(system="s", messages=[{"role": "user", "content": "?"}], model=model, max_tokens=4000))
        assert out == "agents rag"
        assert plain.calls[-1]["max_tokens"] == 4000 + room and "thinking" not in plain.calls[-1]


def test_thinking_turned_off_in_settings(plain):
    """Off: Haiku 5.5 sends thinking "disabled", Sonnet 5.5 "between_tools" (no thinking before the answer), and both
    keep max_tokens as is. Opus 5.5 can't stop thinking: unchanged, with its room."""
    from app import llm

    assert llm.set_thinking(False) == {"enabled": False}
    llm.complete(system="s", prompt="p", max_tokens=200)
    assert plain.calls[-1]["thinking"] == {"type": "disabled"} and plain.calls[-1]["max_tokens"] == 200
    llm.describe_frames([_frame()])
    assert plain.calls[-1]["thinking"] == {"type": "disabled"} and plain.calls[-1]["max_tokens"] == 2000
    llm.transcribe_pdf(b"%PDF-1.4")
    assert plain.calls[-1]["thinking"] == {"type": "disabled"} and plain.calls[-1]["max_tokens"] == 20000

    expected = {"claude-haiku-5-5": ({"type": "disabled"}, 4000), "claude-sonnet-5-5": ({"type": "between_tools"}, 4000),
                "claude-opus-5-5": (None, 4000 + llm.THINKING_ROOM), "claude-haiku-4-5": (None, 4000)}
    for model, (thinking, max_tokens) in expected.items():
        "".join(llm.stream_text(system="s", messages=[{"role": "user", "content": "?"}], model=model, max_tokens=4000))
        assert plain.calls[-1].get("thinking") == thinking and plain.calls[-1]["max_tokens"] == max_tokens

    assert llm.set_thinking(True) == {"enabled": True}
    llm.complete(system="s", prompt="p", max_tokens=200)
    assert "thinking" not in plain.calls[-1]


def test_structured_outputs_follow_the_setting(claude):
    from app import llm

    _write_digest("claude-sonnet-5-5")
    assert "thinking" not in claude.calls[-1]
    llm.set_thinking(False)
    _write_digest("claude-sonnet-5-5")
    assert claude.calls[-1]["thinking"] == {"type": "between_tools"} and "output_config" in claude.calls[-1]
    _write_digest("claude-opus-5-5")
    assert "thinking" not in claude.calls[-1]


def test_thinking_setting_api(clean_db):
    from fastapi.testclient import TestClient

    from app.main import app

    from .test_e2e import AUTH

    with TestClient(app) as c:
        assert c.get("/api/settings/thinking", headers=AUTH).json() == {"enabled": True}
        assert c.put("/api/settings/thinking", json={"enabled": False}, headers=AUTH).json() == {"enabled": False}
        assert c.get("/api/settings/thinking", headers=AUTH).json() == {"enabled": False}
        assert c.put("/api/settings/thinking", json={}, headers=AUTH).status_code == 422
        assert c.get("/api/settings/thinking").status_code == 401


BROKEN_CARD = {
    "title": "Apprendre en 48 h", "summary": "Un étudiant prépare un examen.",
    "key_points": "\n<item>Les questions comptent.</item>\n<item>Trois débats d'experts.</item>\n</item>\n</invoke>",
}
GOOD_CARD = {
    "title": "Apprendre en 48 h", "summary": "Un étudiant prépare un examen.", "key_points": ["Les questions comptent."],
    "tags": ["Learning", "#notebooklm"], "entities": [{"name": "NotebookLM", "type": "product"}, {"type": "person"}],
    "use_cases": ["Utile pour réviser"], "action_items": [], "genre": "thread", "language": "fr",
    "translation": {"title": "Learn in 48 h", "summary": "A student prepares.", "key_points": "<item>Questions matter.</item>",
                    "use_cases": ["Useful to revise"]},
}


def _enrich():
    from app import llm

    return llm.enrich(kind="tweet", title=None, author="@someone", source_url="https://x.com/someone/status/1",
                      published_at=None, content="Un thread sur NotebookLM.", user_note=None, existing_tags=[])


def test_a_card_with_lists_sent_as_text_is_asked_again(monkeypatch):
    """Seen in production: every list of a card came back as one "<item>…</item>" text, and the app crashed on it."""
    from app import llm

    answers = [dict(BROKEN_CARD), dict(GOOD_CARD)]
    monkeypatch.setattr(llm, "call_tool", lambda **kw: answers.pop(0))
    out = _enrich()
    assert answers == []                                            # asked a second time
    assert out["key_points"] == ["Les questions comptent."] and out["tags"] == ["learning", "notebooklm"]
    assert out["entities"] == [{"name": "NotebookLM", "type": "product"}]
    assert out["translations"]["en"]["key_points"] == ["Questions matter."]


def test_a_card_broken_twice_is_cleaned(monkeypatch):
    from app import llm

    monkeypatch.setattr(llm, "call_tool", lambda **kw: dict(BROKEN_CARD))
    out = _enrich()
    assert out["key_points"] == ["Les questions comptent.", "Trois débats d'experts."]
    assert out["use_cases"] == [] and out["tags"] == [] and out["entities"] == [] and out["action_items"] == []
    assert llm._text_list("- Utile pour réviser\n- Utile si examen\n</invoke>") == ["Utile pour réviser", "Utile si examen"]
    assert llm._text_list(None) == [] and llm._text_list(["a", "", 3, {"x": 1}]) == ["a", "3"]
    assert llm._text_list('["un", "deux"]') == ["un", "deux"] and llm._text_list("[pas du JSON") == ["[pas du JSON"]
