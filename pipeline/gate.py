#!/usr/bin/env python3
"""Gate between Android and iOS jobs: firmwares with passing parity (or a parity accept override),
plus current, non-overridable expression proof where configured. Prints a JSON list so scarce macOS
runners build only what can be published; a per-firmware table goes to stderr.

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
from expression_config import proof_errors  # noqa: E402


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
        _, expression_errors = proof_errors(fid, pins[fid]["commit"], b["results"], b["verify"], b["files"], HERE)
        ok = (check in PUBLISHABLE or fid in ov.get("accept", {})) and not expression_errors
        print(f"{fid}: {check}{'' if ok else ' (not published): ' + '; '.join(expression_errors or why)[:160]}",
              file=sys.stderr)
        if ok:
            passed.append(fid)
    print(json.dumps(passed))


if __name__ == "__main__":
    main()
