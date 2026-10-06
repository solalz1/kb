"""The generated .shortcut files: variables wired to earlier actions, requests matching the API."""

import importlib.util
import plistlib
from pathlib import Path

import pytest

BUILD = Path(__file__).resolve().parents[2] / "shortcuts" / "build.py"


@pytest.fixture(scope="module")
def build():
    spec = importlib.util.spec_from_file_location("kb_shortcuts_build", BUILD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def shortcuts(build):
    return {s["WFWorkflowName"]: s for s in build.build_all("https://kb.example.com/")}


def _refs(value):
    """Every variable reference inside a parameter value."""
    if isinstance(value, dict):
        if value.get("Type") == "ActionOutput":
            yield value
        for v in value.values():
            yield from _refs(v)
    elif isinstance(value, list):
        for v in value:
            yield from _refs(v)


def _texts(value):
    if isinstance(value, dict):
        if value.get("WFSerializationType") == "WFTextTokenString":
            yield value["Value"]
        for v in value.values():
            yield from _texts(v)
    elif isinstance(value, list):
        for v in value:
            yield from _texts(v)


def _request(shortcut):
    return next(a["WFWorkflowActionParameters"] for a in shortcut["WFWorkflowActions"]
                if a["WFWorkflowActionIdentifier"].endswith(".downloadurl"))


def _keys(dictionary_field):
    return {row["WFKey"]["Value"]["string"]: row for row in dictionary_field["Value"]["WFDictionaryFieldValueItems"]}


def test_three_shortcuts_round_trip_as_binary_plists(shortcuts):
    assert set(shortcuts) == {"Add To KB", "Fichier vers ma KB", "Note perso"}
    for s in shortcuts.values():
        assert plistlib.loads(plistlib.dumps(s, fmt=plistlib.FMT_BINARY)) == s


def test_variables_point_to_earlier_actions(shortcuts):
    for s in shortcuts.values():
        seen = set()
        for action in s["WFWorkflowActions"]:
            params = action["WFWorkflowActionParameters"]
            for ref in _refs(params):
                assert ref["OutputUUID"] in seen, (s["WFWorkflowName"], action["WFWorkflowActionIdentifier"])
            if "UUID" in params:
                seen.add(params["UUID"])


def test_inline_variables_sit_on_placeholders(build, shortcuts):
    for s in shortcuts.values():
        for value in _texts(s["WFWorkflowActions"]):
            utf16 = value["string"].encode("utf-16-le")
            for rng in value["attachmentsByRange"]:
                start = int(rng.strip("{}").split(",")[0])
                assert utf16[start * 2:start * 2 + 2].decode("utf-16-le") == build.OBJECT


def test_token_is_asked_on_import_never_stored(shortcuts):
    for s in shortcuts.values():
        (question,) = s["WFWorkflowImportQuestions"]
        action = s["WFWorkflowActions"][question["ActionIndex"]]
        assert action["WFWorkflowActionIdentifier"] == "is.workflow.actions.gettext"
        assert question["ParameterKey"] == "WFTextActionText" and action["WFWorkflowActionParameters"]["WFTextActionText"] == ""
        header = _keys(_request(s)["WFHTTPHeaders"])["Authorization"]["WFValue"]["Value"]
        assert header["string"] == "Bearer " + "￼"
        (ref,) = header["attachmentsByRange"].values()
        assert ref["OutputUUID"] == action["WFWorkflowActionParameters"]["UUID"]
        assert "test-token" not in str(s)


def test_requests_match_the_api(shortcuts):
    from app.main import NoteIn, api

    routes = {r.path for r in api.routes if "POST" in getattr(r, "methods", ())}

    add = _request(shortcuts["Add To KB"])
    assert add["WFURL"] == "https://kb.example.com/api/ingest" and add["WFURL"].removeprefix("https://kb.example.com") in routes
    assert add["WFHTTPMethod"] == "POST" and add["WFHTTPBodyType"] == "JSON"
    assert set(_keys(add["WFJSONValues"])) == {"url", "text", "note", "category"}

    files = _request(shortcuts["Fichier vers ma KB"])
    assert files["WFHTTPBodyType"] == "Form"
    form = _keys(files["WFFormValues"])
    assert set(form) == {"file", "note", "category"} and form["file"]["WFItemType"] == 5
    # A File row is a variable wrapped twice: anything else crashes Shortcuts when it loads the file.
    assert form["file"]["WFValue"] == {
        "Value": {"Value": {"Type": "Variable", "VariableName": "Repeat Item"},
                  "WFSerializationType": "WFTextTokenAttachment"},
        "WFSerializationType": "WFTokenAttachmentParameterState"}

    note = _request(shortcuts["Note perso"])
    assert note["WFURL"].removeprefix("https://kb.example.com") in routes
    body = _keys(note["WFJSONValues"])
    assert set(body) <= set(NoteIn.model_fields) and body["space"]["WFValue"]["Value"]["string"] == "perso"


def test_choices_are_understood_by_the_app(build):
    from app import taxonomy

    for word in build.CATEGORIES:
        assert taxonomy.normalize_category(word) in taxonomy.CATEGORIES, word
    assert set(build.CATEGORIES) == {label for label, _, _ in taxonomy.CATEGORIES.values()}
    assert [taxonomy.space_from_word(w) for w in build.PLACES[:2]] == ["main", "perso"]
    assert build.PLACES[2:] == build.CATEGORIES


def test_file_shortcut_reports_the_server_message(shortcuts):
    actions = shortcuts["Fichier vers ma KB"]["WFWorkflowActions"]
    end = next(a["WFWorkflowActionParameters"] for a in actions
               if a["WFWorkflowActionIdentifier"].endswith("repeat.each")
               and a["WFWorkflowActionParameters"]["WFControlFlowMode"] == 2)
    body = actions[-1]["WFWorkflowActionParameters"]["WFNotificationActionBody"]["Value"]
    (ref,) = body["attachmentsByRange"].values()
    assert ref["OutputUUID"] == end["UUID"] and ref["OutputName"] == "Repeat Results"
    # the last action inside the loop reads the server's message, so the loop's results are those messages
    assert actions[-3]["WFWorkflowActionIdentifier"].endswith("getvalueforkey")
    assert actions[-3]["WFWorkflowActionParameters"]["WFDictionaryKey"] == "message"


def test_share_sheet_settings(shortcuts):
    add, files, note = shortcuts["Add To KB"], shortcuts["Fichier vers ma KB"], shortcuts["Note perso"]
    assert add["WFWorkflowTypes"] == ["ActionExtension"]
    assert add["WFWorkflowNoInputBehavior"]["Name"] == "WFWorkflowNoInputBehaviorGetClipboard"
    assert "ActionExtension" in files["WFWorkflowTypes"] and files["WFQuickActionSurfaces"] == ["Finder"]
    assert note["WFWorkflowTypes"] == [] and not note["WFWorkflowHasShortcutInputVariables"]
    modes = [a["WFWorkflowActionParameters"].get("WFControlFlowMode") for a in files["WFWorkflowActions"]
             if a["WFWorkflowActionIdentifier"].endswith("repeat.each")]
    assert modes == [0, 2]
