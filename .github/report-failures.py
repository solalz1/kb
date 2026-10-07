"""Turns pytest's JUnit report into GitHub annotations, so a failed test shows its error on the PR itself."""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main(path: str) -> None:
    if not Path(path).exists():
        print(f"no report at {path}")
        return
    for case in ET.parse(path).iter("testcase"):
        for bad in [*case.findall("failure"), *case.findall("error")]:
            detail = f"{bad.get('message') or ''}\n{bad.text or ''}".strip()
            title = escape(f"{case.get('classname')}.{case.get('name')}")
            print(f"::error title={title}::{escape(detail[-4000:])}")


if __name__ == "__main__":
    main(sys.argv[1])
