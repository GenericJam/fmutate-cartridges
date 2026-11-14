#!/usr/bin/env python3
"""The parity gate between the workflow's Android and iOS jobs: which of this run's firmwares passed the check against
the emulator (publish.py's rule: `match`, `silent`, or accepted in overrides.json), as a JSON list on stdout, so iOS
(macOS runners, the scarce ones) builds only what will be published. A per-firmware table goes to stderr.

    python3 gate.py --artifacts DIR
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from publish import PUBLISHABLE, collect  # noqa: E402
from report import verdict  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifacts", required=True)
    a = ap.parse_args()
    pins = json.loads((HERE / "pins.json").read_text())
    ov = json.loads((HERE / "overrides.json").read_text()) if (HERE / "overrides.json").exists() else {}
    passed = []
    for fid, b in sorted(collect(Path(a.artifacts), pins).items()):
        host = b["results"].get("host") or {}
        if not host.get("ok"):
            print(f"{fid}: host build failed at {host.get('stage')}", file=sys.stderr)
            continue
        check, why = verdict(b["verify"], (host.get("recipe") or {}).get("recipe")) if b["verify"] else \
            ("unchecked", ["no check ran"])
        ok = check in PUBLISHABLE or fid in ov.get("accept", {})
        print(f"{fid}: {check}{'' if ok else ' (not published): ' + '; '.join(why)[:160]}", file=sys.stderr)
        if ok:
            passed.append(fid)
    print(json.dumps(passed))


if __name__ == "__main__":
    main()
