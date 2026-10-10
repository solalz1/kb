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
    """The request that sends the share (the first POST)."""
    return next(a["WFWorkflowActionParameters"] for a in shortcut["WFWorkflowActions"]
                if a["WFWorkflowActionIdentifier"].endswith(".downloadurl")
                and a["WFWorkflowActionParameters"]["WFHTTPMethod"] == "POST")


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
    assert set(_keys(add["WFJSONValues"])) == {"url", "text", "note", "folder", "page_follows"}

    files = _request(shortcuts["Fichier vers ma KB"])
    assert files["WFHTTPBodyType"] == "Form"
    form = _keys(files["WFFormValues"])
    assert set(form) == {"file", "note", "folder"} and form["file"]["WFItemType"] == 5
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


@pytest.mark.parametrize("name", ["Add To KB", "Fichier vers ma KB"])
def test_where_to_file_it_comes_from_the_kb(build, shortcuts, name):
    """"Où le ranger ?" lists the KB's folders as they are when sharing: a folder added in the app shows up without
    rebuilding the Shortcut. The choice goes back as `folder`."""
    from app.main import api

    params = [a["WFWorkflowActionParameters"] for a in shortcuts[name]["WFWorkflowActions"]]
    ids = [a["WFWorkflowActionIdentifier"].removeprefix("is.workflow.actions.") for a in shortcuts[name]["WFWorkflowActions"]]
    i = ids.index("choosefromlist")
    fetch, pick, chosen = params[i - 2], params[i - 1], params[i]
    assert ids[i - 2:i] == ["downloadurl", "getvalueforkey"] and "list" not in ids
    assert fetch["WFURL"] == "https://kb.example.com" + build.CHOICES_PATH and fetch["WFHTTPMethod"] == "GET"
    assert build.CHOICES_PATH in {r.path for r in api.routes if "GET" in getattr(r, "methods", ())}
    assert _keys(fetch["WFHTTPHeaders"])["Authorization"]["WFValue"]["Value"]["string"] == "Bearer " + build.OBJECT
    assert pick["WFDictionaryKey"] == "choices" and pick["WFInput"]["Value"]["OutputUUID"] == fetch["UUID"]
    assert chosen["WFInput"]["Value"]["OutputUUID"] == pick["UUID"]
    assert chosen["WFChooseFromListActionPrompt"] == "Où le ranger ?"
    request = _request(shortcuts[name])
    body = _keys(request.get("WFJSONValues") or request["WFFormValues"])
    (ref,) = body["folder"]["WFValue"]["Value"]["attachmentsByRange"].values()
    assert ref["OutputUUID"] == chosen["UUID"]


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


def test_add_fetches_the_page_from_the_phone_when_asked(build, shortcuts):
    """A site that refuses servers: the API answers page_wanted, and only then the phone fetches the page and posts it
    to /api/items/<id>/page. The share is saved and reported before, so this part can't lose it."""
    from app.main import api

    actions = shortcuts["Add To KB"]["WFWorkflowActions"]
    ids = [a["WFWorkflowActionIdentifier"].removeprefix("is.workflow.actions.") for a in actions]
    start = next(i for i, a in enumerate(actions) if ids[i] == "conditional"
                 and a["WFWorkflowActionParameters"]["WFControlFlowMode"] == 0)
    end = next(i for i, a in enumerate(actions) if ids[i] == "conditional"
               and a["WFWorkflowActionParameters"]["WFControlFlowMode"] == 2)
    assert ids.index("notification") < start < end == len(actions) - 1
    assert ids[start + 1:end] == ["getvalueforkey", "getvalueforkey", "downloadurl", "downloadurl"]

    params = [a["WFWorkflowActionParameters"] for a in actions]
    cond = params[start]
    assert cond["WFCondition"] == build.HAS_ANY_VALUE and cond["GroupingIdentifier"] == params[end]["GroupingIdentifier"]
    asked = cond["WFInput"]["Variable"]["Value"]["OutputUUID"]
    assert next(p for p in params if p.get("UUID") == asked)["WFDictionaryKey"] == "page_wanted"

    address, fetch, send = params[start + 2], params[start + 3], params[start + 4]
    assert address["WFDictionaryKey"] == "page_url"          # the address the server resolved, not Get URLs
    assert fetch["WFHTTPMethod"] == "GET" and list(fetch["WFURL"]["Value"]["attachmentsByRange"].values())[0][
        "OutputUUID"] == address["UUID"]
    assert "iPhone" in _keys(fetch["WFHTTPHeaders"])["User-Agent"]["WFValue"]["Value"]["string"]
    url = send["WFURL"]["Value"]
    assert url["string"] == "https://kb.example.com/api/items/" + build.OBJECT + "/page"
    assert list(url["attachmentsByRange"].values())[0]["OutputUUID"] == params[start + 1]["UUID"]
    assert params[start + 1]["WFDictionaryKey"] == "id"
    routes = {r.path for r in api.routes if "POST" in getattr(r, "methods", ())}
    assert "/api/items/{item_id}/page" in routes
    page = _keys(send["WFFormValues"])["page"]
    assert page["WFItemType"] == 5 and page["WFValue"]["Value"]["Value"]["OutputUUID"] == fetch["UUID"]
    assert _keys(send["WFHTTPHeaders"])["Authorization"]["WFValue"]["Value"]["string"] == "Bearer " + build.OBJECT


def test_get_urls_reads_the_shared_input(shortcuts):
    """Get URLs from Input takes a text parameter: given a bare variable, Shortcuts shows an empty "Input" and the
    action gets nothing (seen on a Mac: no URL, so no page fetched from the phone)."""
    params = next(a["WFWorkflowActionParameters"] for a in shortcuts["Add To KB"]["WFWorkflowActions"]
                  if a["WFWorkflowActionIdentifier"].endswith("detect.link"))
    value = params["WFInput"]
    assert value["WFSerializationType"] == "WFTextTokenString"
    assert list(value["Value"]["attachmentsByRange"].values()) == [{"Type": "ExtensionInput"}]
