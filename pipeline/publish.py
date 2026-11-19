#!/usr/bin/env python3
"""Assemble and sign the cartridge index (schema 1, FMutate docs/decisions/2026-11-14-native-catalog.md "The index").

    python3 publish.py --artifacts DIR --tag TAG --out DIST (--prev INDEX | --first) [--key signing.pem]

DIR holds what the build jobs uploaded, one directory per job, named as the workflow names its artifacts:
cart-android-<id> (results/<id>.<host|android-*>.json, out/<platform>/libcart_<id>.so, verify/<id>.json) and
cart-ios-<id> (results/<id>.ios-*.json, out/ios-*/libcart_<id>.a). Only <id>'s own files are taken from a job's
directory, so one firmware's build (which runs that firmware's own scripts) can't put files in another's entry.

A firmware is published when its check against the emulator is `match` or `silent` (report.py's verdicts), or
overrides.json "accept" names it with the reason a person checked; anything else goes to the index's "tried" list with
the reason, so the next run doesn't build the same commit again. Firmwares not built in this run keep their entries
from --prev, the published index (a file: the workflow reads the gh-pages branch's copy), whose signature (INDEX.sig)
must verify against signing.pub.pem; --first is the one run with no index yet.

Writes DIST/assets/<release>/libcart_<id>-<version>-<platform>-<sha256[:12]>.<so|a> (a name per content, so a re-run
adding to a release never replaces a file an index names) and DIST/index.json + index.json.sig, signed
with --key (Ed25519 PEM, `openssl genpkey -algorithm ed25519`) through openssl; each asset's signature is in its index
entry ("sig"). A run's assets go to release TAG, then TAG-2, TAG-3 ... PER_RELEASE at a time (GitHub keeps at most
1000 assets in a release); release.py uploads them. Asset URLs are
https://github.com/<repo>/releases/download/<release>/<file>.
"""
import argparse
import base64
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from report import verdict  # noqa: E402

REPO = "GenericJam/fmutate-cartridges"
PUBKEY = HERE / "signing.pub.pem"
PUBLISHABLE = {"match", "silent"}
KINDS = {"android": ("host", "android-arm64-v8a", "android-x86_64", "android-armeabi-v7a"),
         "ios": ("ios-device", "ios-sim")}
PLATFORMS = KINDS["android"][1:] + KINDS["ios"]
SUFFIX = {"android": ".so", "ios": ".a"}
JOB = re.compile(r"cart-(android|ios)-([A-Za-z0-9_.-]+)")
PER_RELEASE = 900                               # (and the run's pins.json beside them)


def sign(key, path):
    sig = subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", str(key), "-rawin", "-in", str(path)],
                         capture_output=True, check=True).stdout
    return base64.b64encode(sig).decode()


def verified(path, sig_b64, pub=PUBKEY):
    with tempfile.NamedTemporaryFile() as s:
        s.write(base64.b64decode(sig_b64.strip()))
        s.flush()
        return subprocess.run(["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(pub), "-rawin",
                               "-in", str(path), "-sigfile", s.name], capture_output=True).returncode == 0


def load_prev(src, first):
    if first:
        return {"firmwares": [], "tried": []}
    p = Path(src)
    sig = Path(src + ".sig")
    if not p.is_file() or not sig.is_file():
        raise SystemExit(f"publish: no previous index at {src} (+ .sig); --first only for the first run")
    if not verified(p, sig.read_text()):
        raise SystemExit(f"publish: the previous index {src} doesn't verify against {PUBKEY.name}")
    return json.loads(p.read_text())


def own_file(path):
    return path.is_file() and not path.is_symlink()


def collect(art, pins):
    """{id: {"results": {target: result}, "files": {platform: path}, "verify": dict or None}} from the job dirs"""
    built = {}
    for job in sorted(art.iterdir()):
        m = JOB.fullmatch(job.name)
        if not m or not job.is_dir() or m.group(2) not in pins:
            continue
        kind, fid = m.groups()
        b = built.setdefault(fid, {"results": {}, "files": {}, "verify": None})
        for target in KINDS[kind]:
            rj = next((p for p in job.rglob(f"results/{fid}.{target}.json") if own_file(p)), None)
            r = json.loads(rj.read_text()) if rj else None
            if not isinstance(r, dict) or r.get("id") != fid or r.get("target") != target:
                continue
            b["results"][target] = r
            if target != "host":
                f = next((p for p in job.rglob(f"out/{target}/libcart_{fid}{SUFFIX[kind]}") if own_file(p)), None)
                if r.get("ok") and f:
                    b["files"][target] = f
        if kind == "android":
            vj = next((p for p in job.rglob(f"verify/{fid}.json") if own_file(p)), None)
            b["verify"] = json.loads(vj.read_text()) if vj else None
    return built


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifacts", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--prev")
    ap.add_argument("--first", action="store_true")
    ap.add_argument("--key")
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--hub-updated")
    a = ap.parse_args()
    if not a.first and not a.prev:
        ap.error("--prev INDEX or --first")
    pins = json.loads((HERE / "pins.json").read_text())
    ov = json.loads((HERE / "overrides.json").read_text()) if (HERE / "overrides.json").exists() else {}
    accept = ov.get("accept", {})
    out = Path(a.out)
    (out / "assets").mkdir(parents=True, exist_ok=True)
    prev = load_prev(a.prev, a.first)
    entries = {f["id"]: f for f in prev.get("firmwares", [])}
    tried = {f["id"]: f for f in prev.get("tried", [])}
    today = date.today().isoformat()

    built = collect(Path(a.artifacts), pins)
    if not built:
        raise SystemExit("publish: no firmware artifacts collected; refusing to write an empty index")
    n_assets = 0

    for fid, b in sorted(built.items()):
        pin = pins[fid]
        host_r = b["results"].get("host") or {}
        recipe = host_r.get("recipe") or {}
        v = b["verify"]
        check, why = verdict(v, recipe.get("recipe")) if v else ("unchecked", ["no check ran"])
        ok = check in PUBLISHABLE or fid in accept
        base = {"id": fid, "version": pin["version"], "repo": pin["repo"], "commit": pin["commit"], "pin": pin["how"]}
        if not ok or not b["files"]:
            reason = "; ".join(why) if not ok else "no platform built: " + json.dumps(
                {t: r.get("stage") for t, r in b["results"].items()})
            if entries.get(fid, {}).get("commit") == pin["commit"]:
                continue                    # (a rebuild of what's published that failed this time: keep that)
            tried[fid] = {**base, "check": check, "reason": reason[:300], "date": today}
            continue
        assets = {}
        for platform, src in sorted(b["files"].items()):
            sha = hashlib.sha256(src.read_bytes()).hexdigest()
            name = re.sub(r"[^A-Za-z0-9._-]", "_", f"libcart_{fid}-{pin['version']}-{platform}-{sha[:12]}{src.suffix}")
            release = a.tag if n_assets < PER_RELEASE else f"{a.tag}-{n_assets // PER_RELEASE + 1}"
            n_assets += 1
            dst = out / "assets" / release / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            assets[platform] = {"url": f"https://github.com/{a.repo}/releases/download/{release}/{name}",
                                "sha256": sha, "size": dst.stat().st_size, "sig": sign(a.key, dst) if a.key else ""}
        tried.pop(fid, None)
        c = (v or {}).get("compare", {})
        old = entries.get(fid, {})
        entries[fid] = {
            **base, "name": pin.get("name") or fid, "license": pin["lic"], "recipe": recipe.get("recipe"),
            "build": "patch" if host_r.get("patches") else "shims" if host_r.get("shims") else "clean",
            "patches": host_r.get("patches", []),
            "parity": {"status": check if check in PUBLISHABLE else "accepted", "level_diff_db": c.get("level_diff_db"),
                       "spectrum_corr": c.get("spectrum_corr"),
                       "screen_diff": max([c.get(k, 0) for k in c if k.startswith("screen_diff")] or [0]),
                       "notes": accept.get(fid) or "; ".join(why)},
            "added": old.get("added", today), "updated": today,
            "assets": {**(old.get("assets", {}) if old.get("commit") == pin["commit"] else {}), **assets},
        }

    index = {"schema": 1, "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "engine_abi": 1,
             "hub_updated": a.hub_updated or prev.get("hub_updated"),
             "firmwares": [entries[k] for k in sorted(entries)], "tried": [tried[k] for k in sorted(tried)]}
    ip = out / "index.json"
    ip.write_text(json.dumps(index, indent=1) + "\n")
    if a.key:
        (out / "index.json.sig").write_text(sign(a.key, ip) + "\n")
    n = {p: sum(1 for f in index["firmwares"] if p in f["assets"]) for p in PLATFORMS}
    print(f"index: {len(index['firmwares'])} published, {len(index['tried'])} tried; "
          f"{n_assets} assets this run; "
          f"published per platform {json.dumps(n)}")


if __name__ == "__main__":
    main()
