"""Regenerate daily_vibe/resources/emoji.json from Unicode sources.

Sources (both published by the Unicode Consortium):
  * emoji-test.txt  - https://unicode.org/Public/emoji/latest/emoji-test.txt
    (groups, order, CLDR short names, emoji version)
  * CLDR annotations - https://raw.githubusercontent.com/unicode-org/cldr-json/main/
    cldr-json/cldr-annotations-full/annotations/en/annotations.json  (keywords)

Only fully-qualified emoji are kept; skin-tone variants and the "Component"
group are dropped. Output: a compact JSON list of
  {"e": emoji, "n": name, "g": group, "k": [keywords], "v": emoji version}

Usage: python scripts/build_emoji_data.py [emoji-test.txt] [annotations.json]
(downloads when paths are not given).
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

TEST_URL = "https://unicode.org/Public/emoji/latest/emoji-test.txt"
ANN_URL = ("https://raw.githubusercontent.com/unicode-org/cldr-json/main/cldr-json/"
           "cldr-annotations-full/annotations/en/annotations.json")
OUT = Path(__file__).resolve().parent.parent / "daily_vibe" / "resources" / "emoji.json"
SKIN = re.compile("[\U0001F3FB-\U0001F3FF]")
LINE = re.compile(r"^([0-9A-F ]+?)\s*;\s*fully-qualified\s*#\s*(\S+)\s+E(\d+\.\d+)\s+(.*)$")


def _read(arg: str | None, url: str) -> str:
    if arg:
        return Path(arg).read_text(encoding="utf-8")
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read().decode("utf-8")


def build(test_txt: str, ann: dict) -> list[dict]:
    annotations = ann["annotations"]["annotations"]
    out, group = [], ""
    for line in test_txt.splitlines():
        if line.startswith("# group:"):
            group = line.split(":", 1)[1].strip()
            continue
        m = LINE.match(line)
        if not m or group == "Component":
            continue
        emoji, version, name = m.group(2), m.group(3), m.group(4).strip()
        if SKIN.search(emoji):
            continue
        a = annotations.get(emoji) or annotations.get(emoji.replace("\ufe0f", "")) or {}
        name = name.replace("flag: ", "flag ")
        kw = [k for k in a.get("default", []) if k.lower() != name.lower()]
        out.append({"e": emoji, "n": name, "g": group, "k": kw, "v": float(version)})
    return out


def main(argv: list[str]) -> None:
    test_txt = _read(argv[1] if len(argv) > 1 else None, TEST_URL)
    ann = json.loads(_read(argv[2] if len(argv) > 2 else None, ANN_URL))
    data = build(test_txt, ann)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {len(data)} emoji to {OUT}")


if __name__ == "__main__":
    main(sys.argv)
