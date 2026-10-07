"""Structured answers from Claude: a forced tool call, or structured outputs on models that refuse forced tools."""

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
    """Haiku 4.5 accepts a forced tool; Sonnet 5.5 and Opus 5.5 refuse it, think first, and answer in JSON text."""

    def __init__(self, answer, stop_reason=None):
        self.answer = answer
        self.stop_reason = stop_reason
        self.calls = []
        self.messages = SimpleNamespace(create=self._call, stream=self._stream)

    def _call(self, **kw):
        self.calls.append(kw)
        forced = (kw.get("tool_choice") or {}).get("type") in ("tool", "any")
        if kw["model"] != "claude-haiku-4-5":
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
def claude(monkeypatch):
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
    out = _write_digest("claude-haiku-4-5", max_tokens=2000)
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
    _write_digest("claude-haiku-4-5", max_tokens=2000)
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
