#!/usr/bin/env python3
"""Which firmwares a CI run builds: those whose pin differs from the published index's (a new Hub entry, a new
version, a new pin), plus any named; as a JSON list on stdout (a GitHub Actions matrix).

    python3 plan.py [--index FILE] [--all] [id ...]

The published index is read from --index (the workflow passes the gh-pages branch's copy); none (the first run)
means everything. Named ids that aren't pinned firmwares are dropped (so a dispatch input can't name anything else).
`overrides.json` (beside this file) can hold {"skip": {id: reason}}: firmwares not to build (for example a
licence question), listed in the report rather than built.
"""
import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SAFE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")      # (ids go into job names and paths; the Hub's catalogue is input)
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
SHA = re.compile(r"[0-9a-f]{40}")


def published(src):
    if src and Path(src).is_file():
        return json.loads(Path(src).read_text())
    print("plan: no published index; building all", file=sys.stderr)
    return {"firmwares": []}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("ids", nargs="*")
    a = ap.parse_args()
    pins = {k: p for k, p in json.loads((HERE / "pins.json").read_text()).items()
            if SAFE.fullmatch(k) and REPO.fullmatch(p.get("repo") or "") and SHA.fullmatch(p.get("commit") or "")}
    ov = json.loads((HERE / "overrides.json").read_text()) if (HERE / "overrides.json").exists() else {}
    skip = ov.get("skip", {})
    if a.ids:
        todo = [i for i in dict.fromkeys(a.ids) if i in pins]
    elif a.all:
        todo = list(pins)
    else:
        idx = published(a.index)
        known = {f["id"]: f for f in idx.get("tried", []) + idx.get("firmwares", [])}
        todo = [fid for fid, p in pins.items()
                if (known.get(fid) or {}).get("commit") != p["commit"]
                or (known.get(fid) or {}).get("version") != p["version"]]
    print(json.dumps([i for i in todo if i not in skip]))


if __name__ == "__main__":
    main()
