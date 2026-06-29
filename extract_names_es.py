"""Dev tool: build the bundled Spanish item-name map (names_es.json).

ao-bin-dumps ships English item names in items_raw.txt but the localized names
live in a separate 90 MB localization.json (TMX format). Run once to regenerate
names_es.json (id -> Spanish name) for every id the app already knows:

    curl -sL https://raw.githubusercontent.com/ao-data/ao-bin-dumps/master/localization.json -o localization_full.json
    python extract_names_es.py

localization_full.json is dev-only (gitignored), like items_full.json. Each
item's Spanish name is under tuid "@ITEMS_<uniquename>"; enchant variants
(T4_MAIN_SWORD@1) share the base token, so we strip a trailing "@N".
"""
import json
import re

_LINE_RE = re.compile(r"^\s*\d+:\s+(\S+)\s*:\s*(.+?)\s*$")


def _build_es_tokens(path: str) -> dict[str, str]:
    data = json.load(open(path, encoding="utf-8"))
    es: dict[str, str] = {}
    for entry in data["tmx"]["body"]["tu"]:
        tuid = entry.get("@tuid", "")
        if not tuid.startswith("@ITEMS_"):
            continue
        tuv = entry["tuv"]
        if isinstance(tuv, dict):
            tuv = [tuv]
        for v in tuv:
            if isinstance(v, dict) and v.get("@xml:lang") == "ES-ES" and isinstance(v.get("seg"), str):
                es[tuid] = v["seg"]
                break
    return es


def main():
    es = _build_es_tokens("localization_full.json")
    names_es: dict[str, str] = {}
    with open("items_raw.txt", encoding="utf-8") as f:
        for line in f:
            m = _LINE_RE.match(line)
            if not m:
                continue
            iid = m.group(1)
            token = "@ITEMS_" + iid.split("@")[0]
            spanish = es.get(token)
            if spanish:
                names_es[iid] = spanish
    json.dump(names_es, open("names_es.json", "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    print(f"wrote names_es.json: {len(names_es)} names")


if __name__ == "__main__":
    main()
