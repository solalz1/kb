#!/usr/bin/env python3
"""Build the three KB Shortcuts as .shortcut files.

    python3 shortcuts/build.py --url https://kb.example.com          # unsigned files in shortcuts/dist/
    python3 shortcuts/build.py --url https://kb.example.com --sign   # on a Mac: also signed, ready to double-click

iOS and macOS only import signed Shortcuts, and only macOS can sign them (`shortcuts sign`). Import the signed files on
the Mac: they reach the iPhone through iCloud. The API token is never written into the files: Shortcuts asks for it
when you import each one (it ends up in the first action, "Text", which you can edit later).

Standard library only, so it runs on a stock Mac.
"""

from __future__ import annotations

import argparse
import platform
import plistlib
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

OBJECT = "￼"                      # where an inline variable sits inside a Shortcuts text field
CLIENT_VERSION = "3036.0.4.2"          # Shortcuts on iOS 18; newer versions open it fine
TEXT, FILE = 0, 5                      # WFItemType of a request field (Text / File)
TOKEN_QUESTION = "Colle ton KB_API_TOKEN (app KB → Réglages → Copier le jeton)."
SHORTCUT_INPUT = {"Type": "ExtensionInput"}
REPEAT_ITEM = {"Type": "Variable", "VariableName": "Repeat Item"}
COLORS = {"red": 4282601983, "blue": 463140863, "taupe": 2846468607}
GLYPHS = {"bookmark": 59670, "paperclip": 59794, "microphone": 59780}
CATEGORIES = ["Principe", "Valeur", "Leçon", "Objectif", "Habitude", "Réflexion", "Journal", "Citation", "Ressource"]
# "Où le ranger ?": sent as `category`; the API reads Veille / Perso as a space and the rest as a Perso category
PLACES = ["Veille", "Perso", *CATEGORIES]
HAS_ANY_VALUE = 100                    # WFCondition of an If: "has any value"
# Pages fetched from the phone (sites that refuse servers) ask as Safari on an iPhone would
PHONE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/18.0 Mobile/15E148 Safari/604.1")


def new_uuid() -> str:
    return str(uuid.uuid4()).upper()


def attachment(ref: dict) -> dict:
    """A parameter holding a single variable."""
    return {"Value": ref, "WFSerializationType": "WFTextTokenAttachment"}


def text(*parts: str | dict) -> dict:
    """A text parameter mixing literal strings and variables (dicts)."""
    string, attachments = "", {}
    for part in parts:
        if isinstance(part, str):
            string += part
        else:
            attachments[f"{{{len(string.encode('utf-16-le')) // 2}, 1}}"] = part
            string += OBJECT
    return {"Value": {"string": string, "attachmentsByRange": attachments}, "WFSerializationType": "WFTextTokenString"}


def field(key: str, *value: str | dict, item_type: int = TEXT) -> dict:
    """One row of a JSON body, form body or headers. A File row wraps its variable once more, or Shortcuts crashes
    on import ("State for WFPropertyListParameterValue is not of the expected class")."""
    if item_type == FILE:
        wf_value = {"Value": attachment(value[0]), "WFSerializationType": "WFTokenAttachmentParameterState"}
    else:
        wf_value = text(*value)
    return {"WFItemType": item_type, "WFKey": text(key), "WFValue": wf_value}


def fields(*rows: dict) -> dict:
    return {"Value": {"WFDictionaryFieldValueItems": list(rows)}, "WFSerializationType": "WFDictionaryFieldValue"}


class Builder:
    def __init__(self, name: str):
        self.name = name
        self.actions: list[dict] = []
        self.questions: list[dict] = []

    def add(self, identifier: str, output: str | None = None, *, custom_name: str | None = None,
            with_uuid: bool = True, **params) -> dict | None:
        """Append an action; returns a reference to its output when it has one."""
        uid = new_uuid()
        if with_uuid:
            params["UUID"] = uid
        if custom_name:
            params["CustomOutputName"] = custom_name
        self.actions.append({"WFWorkflowActionIdentifier": f"is.workflow.actions.{identifier}",
                             "WFWorkflowActionParameters": params})
        if not output:
            return None
        return {"Type": "ActionOutput", "OutputUUID": uid, "OutputName": custom_name or output}

    def ask_on_import(self, parameter: str, question: str, default: str = "") -> None:
        """Shortcuts asks this when the file is imported and fills the last action's parameter with the answer."""
        self.questions.append({"ActionIndex": len(self.actions) - 1, "Category": "Parameter",
                               "ParameterKey": parameter, "Text": question, "DefaultValue": default})

    def if_has_value(self, ref: dict) -> str:
        """Start an If block on "<ref> has any value"; close it with end_if(<the returned group>)."""
        group = new_uuid()
        self.add("conditional", with_uuid=False, GroupingIdentifier=group, WFControlFlowMode=0,
                 WFCondition=HAS_ANY_VALUE, WFInput={"Type": "Variable", "Variable": attachment(ref)})
        return group

    def end_if(self, group: str) -> None:
        self.add("conditional", GroupingIdentifier=group, WFControlFlowMode=2)

    def choose(self, prompt: str, items: list[str]) -> dict:
        """A List action followed by Choose from List: one tap instead of typing."""
        options = self.add("list", "List", WFItems=[{"WFItemType": TEXT, "WFValue": text(i)} for i in items])
        return self.add("choosefromlist", "Chosen Item", WFInput=attachment(options), WFChooseFromListActionPrompt=prompt)

    def token(self) -> dict:
        ref = self.add("gettext", "Text", custom_name="Jeton KB", WFTextActionText="")
        self.ask_on_import("WFTextActionText", TOKEN_QUESTION)
        return ref

    def build(self, *, color: str, glyph: str, types: list[str], inputs: list[str],
              no_input: dict | None = None, quick_actions: list[str] | None = None) -> dict:
        plist = {
            "WFWorkflowName": self.name,
            "WFWorkflowClientVersion": CLIENT_VERSION,
            "WFWorkflowMinimumClientVersion": 900,
            "WFWorkflowMinimumClientVersionString": "900",
            "WFWorkflowIcon": {"WFWorkflowIconStartColor": COLORS[color], "WFWorkflowIconGlyphNumber": GLYPHS[glyph]},
            "WFWorkflowImportQuestions": self.questions,
            "WFWorkflowTypes": types,
            "WFQuickActionSurfaces": quick_actions or [],
            "WFWorkflowInputContentItemClasses": inputs,
            "WFWorkflowOutputContentItemClasses": [],
            "WFWorkflowHasShortcutInputVariables": bool(inputs),
            "WFWorkflowHasOutputFallback": False,
            "WFWorkflowActions": self.actions,
        }
        if no_input:
            plist["WFWorkflowNoInputBehavior"] = no_input
        return plist


def headers(token: dict) -> dict:
    return fields(field("Authorization", "Bearer ", token))


def add_to_kb(base: str) -> dict:
    """Share sheet, links and text: POST /api/ingest as JSON. For a site that refuses servers (Medium…), the API answers
    `page_wanted`: the phone then fetches the page itself and sends it to POST /api/items/<id>/page."""
    b = Builder("Add To KB")
    token = b.token()
    # "Get URLs from Input" takes a text parameter: the variable goes inside a text, or Shortcuts shows an empty
    # "Input" and the action gets nothing
    urls = b.add("detect.link", "URLs", WFInput=text(SHORTCUT_INPUT))
    first = b.add("getitemfromlist", "Item from List", WFItemSpecifier="First Item", WFInput=attachment(urls))
    shared = b.add("detect.text", "Text", WFInput=attachment(SHORTCUT_INPUT))
    why = b.add("ask", "Provided Input", WFAskActionPrompt="Pourquoi tu gardes ça ?", WFInputType="Text")
    where = b.choose("Où le ranger ?", PLACES)
    response = b.add("downloadurl", "Contents of URL", WFURL=f"{base}/api/ingest", WFHTTPMethod="POST",
                     ShowHeaders=True, WFHTTPHeaders=headers(token), WFHTTPBodyType="JSON",
                     WFJSONValues=fields(field("url", first), field("text", shared), field("note", why),
                                         field("category", where), field("page_follows", "1")))
    message = b.add("getvalueforkey", "Dictionary Value", WFGetDictionaryValueType="Value",
                    WFDictionaryKey="message", WFInput=attachment(response))
    b.add("notification", WFNotificationActionBody=text(message))
    # the share is saved and reported above; the rest only helps with sites that refuse the server
    wanted = b.add("getvalueforkey", "Dictionary Value", custom_name="Page demandée", WFGetDictionaryValueType="Value",
                   WFDictionaryKey="page_wanted", WFInput=attachment(response))
    group = b.if_has_value(wanted)
    item = b.add("getvalueforkey", "Dictionary Value", custom_name="Élément", WFGetDictionaryValueType="Value",
                 WFDictionaryKey="id", WFInput=attachment(response))
    page = b.add("downloadurl", "Contents of URL", custom_name="Page", WFURL=text(first), WFHTTPMethod="GET",
                 ShowHeaders=True, WFHTTPHeaders=fields(field("User-Agent", PHONE_UA),
                                                        field("Accept-Language", "fr-FR,fr;q=0.9,en;q=0.8")))
    b.add("downloadurl", "Contents of URL", WFURL=text(f"{base}/api/items/", item, "/page"), WFHTTPMethod="POST",
          ShowHeaders=True, WFHTTPHeaders=headers(token), WFHTTPBodyType="Form",
          WFFormValues=fields(field("page", page, item_type=FILE)))
    b.end_if(group)
    return b.build(color="red", glyph="bookmark", types=["ActionExtension"],
                   inputs=["WFURLContentItem", "WFSafariWebPageContentItem", "WFStringContentItem",
                           "WFRichTextContentItem"],
                   no_input={"Name": "WFWorkflowNoInputBehaviorGetClipboard"})


def file_to_kb(base: str) -> dict:
    """Share sheet (and Finder Quick Action), files: one multipart POST /api/ingest per file, then the server's
    message for each one."""
    b = Builder("Fichier vers ma KB")
    token = b.token()
    why = b.add("ask", "Provided Input", WFAskActionPrompt="Pourquoi tu gardes ça ?", WFInputType="Text")
    where = b.choose("Où le ranger ?", PLACES)
    group = new_uuid()
    b.add("repeat.each", with_uuid=False, WFControlFlowMode=0, GroupingIdentifier=group,
          WFInput=attachment(SHORTCUT_INPUT))
    response = b.add("downloadurl", "Contents of URL", WFURL=f"{base}/api/ingest", WFHTTPMethod="POST",
                     ShowHeaders=True, WFHTTPHeaders=headers(token), WFHTTPBodyType="Form",
                     WFFormValues=fields(field("file", REPEAT_ITEM, item_type=FILE), field("note", why),
                                         field("category", where)))
    b.add("getvalueforkey", "Dictionary Value", WFGetDictionaryValueType="Value", WFDictionaryKey="message",
          WFInput=attachment(response))
    # the loop's result is the server's message for each file, success or error
    results = b.add("repeat.each", "Repeat Results", WFControlFlowMode=2, GroupingIdentifier=group)
    b.add("notification", WFNotificationActionBody=text(results))
    return b.build(color="blue", glyph="paperclip", types=["ActionExtension", "QuickActions"],
                   quick_actions=["Finder"],
                   inputs=["WFImageContentItem", "WFPDFContentItem", "WFGenericFileContentItem", "WFAVAssetContentItem"],
                   no_input={"Name": "WFWorkflowNoInputBehaviorAskForInput",
                             "Parameters": {"ItemClass": "WFGenericFileContentItem"}})


def personal_note(base: str) -> dict:
    """Siri or Home Screen: dictate a Perso note, pick its category, POST /api/notes."""
    b = Builder("Note perso")
    token = b.token()
    dictated = b.add("dictatetext", "Dictated Text", WFSpeechLanguage="fr-FR")
    chosen = b.choose("Catégorie ?", CATEGORIES)
    response = b.add("downloadurl", "Contents of URL", WFURL=f"{base}/api/notes", WFHTTPMethod="POST",
                     ShowHeaders=True, WFHTTPHeaders=headers(token), WFHTTPBodyType="JSON",
                     WFJSONValues=fields(field("content", dictated), field("space", "perso"),
                                         field("category", chosen)))
    message = b.add("getvalueforkey", "Dictionary Value", WFGetDictionaryValueType="Value",
                    WFDictionaryKey="message", WFInput=attachment(response))
    b.add("notification", WFNotificationActionBody=text(message))
    return b.build(color="taupe", glyph="microphone", types=[], inputs=[])


SHORTCUTS = [add_to_kb, file_to_kb, personal_note]


def build_all(base: str) -> list[dict]:
    return [make(base.rstrip("/")) for make in SHORTCUTS]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", required=True, help="address of your KB, e.g. https://kb.example.com")
    parser.add_argument("--out", type=Path, default=Path(__file__).with_name("dist"), help="output folder")
    parser.add_argument("--sign", action="store_true", help="sign the files with macOS `shortcuts sign`")
    args = parser.parse_args()
    if not args.url.startswith("https://"):
        parser.error("--url must start with https://")

    unsigned = args.out / "unsigned"
    unsigned.mkdir(parents=True, exist_ok=True)
    paths = []
    for plist in build_all(args.url):
        path = unsigned / f"{plist['WFWorkflowName']}.shortcut"
        path.write_bytes(plistlib.dumps(plist, fmt=plistlib.FMT_BINARY))
        paths.append(path)
        print(f"wrote {path}")

    if not args.sign:
        print("\nOn a Mac, sign them: python3 shortcuts/build.py --url ... --sign")
        return 0
    if platform.system() != "Darwin" or not shutil.which("shortcuts"):
        print("Signing needs macOS (the `shortcuts` command).", file=sys.stderr)
        return 1
    for path in paths:
        signed = args.out / path.name           # same file name: Shortcuts names the shortcut after it
        subprocess.run(["shortcuts", "sign", "--mode", "anyone", "--input", str(path), "--output", str(signed)],
                       check=True)
        print(f"signed {signed}")
    print(f"\nDouble-click each file in {args.out} to import it (Shortcuts asks for your token).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
