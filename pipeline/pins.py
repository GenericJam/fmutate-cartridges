#!/usr/bin/env python3
"""Pin every FM-1 Firmware Hub firmware to a source commit.

    python3 pipeline/pins.py [catalog.json] [--index INDEX]     -> pipeline/pins.json

For each catalogue entry: the commit its package was built from, in this order of trust:
  override    overrides.json {"pin": {id: {"version", "commit", "why"}}}: a person found the package's source commit
              (for example in the firmware's own release record) where the catalogue names none; for that version only
  provenance  the Hub's verified build provenance (`provenance.commit`, a signed CI build of that commit)
  srcref      the Hub's own source reference
  tag         the entry's release tag (`tag`), resolved with `git ls-remote` (annotated tags dereferenced)
  head        the default branch's HEAD today: the entry names no tag, so the package's exact source is unknown
An entry whose Hub version is unchanged keeps its pin, from pins.json here or from --index (the published index,
its signature already checked: what was built, published or tried), so re-running doesn't move a HEAD pin; a new
version resolves again. The catalogue defaults to https://thefirmwarehub.com/catalog.json.
"""
import argparse
import json
import re
import subprocess
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
PINS = HERE / "pins.json"
SHA = re.compile(r"[0-9a-f]{40}")
HUB = "https://thefirmwarehub.com/catalog.json"


def load_catalog(arg):
    if arg and not arg.startswith("http"):
        return json.loads(Path(arg).read_text())
    with urllib.request.urlopen(arg or HUB, timeout=60) as r:
        return json.loads(r.read())


def ls_remote(url, *refs):
    out = subprocess.run(["git", "ls-remote", url, *refs], capture_output=True, text=True, timeout=120,
                         env={"GIT_TERMINAL_PROMPT": "0", "PATH": "/usr/bin:/bin:/opt/homebrew/bin"})
    if out.returncode:
        raise RuntimeError(out.stderr.strip()[:200])
    return dict(reversed(line.split("\t")) for line in out.stdout.splitlines() if "\t" in line)


def resolve(fw):
    url = "https://github.com/%s.git" % fw["repo"]
    prov = fw.get("provenance") or {}
    if prov.get("commit") and prov.get("repo", "").lower() == fw["repo"].lower():
        return {"commit": prov["commit"], "how": "provenance", "ref": prov.get("ref")}
    if fw.get("srcref"):
        return {"commit": fw["srcref"], "how": "srcref", "ref": None}
    if fw.get("tag"):
        tag = "refs/tags/" + fw["tag"]
        refs = ls_remote(url, tag, tag + "^{}")
        commit = refs.get(tag + "^{}") or refs.get(tag)
        if commit:
            return {"commit": commit, "how": "tag", "ref": tag}
    refs = ls_remote(url, "HEAD")
    return {"commit": refs["HEAD"], "how": "head", "ref": "HEAD@" + date.today().isoformat()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("catalog", nargs="?")
    ap.add_argument("--index", help="the published index, whose pins are kept for unchanged versions")
    a = ap.parse_args()
    cat = load_catalog(a.catalog)
    old = json.loads(PINS.read_text()) if PINS.exists() else {}
    idx = json.loads(Path(a.index).read_text()) if a.index and Path(a.index).is_file() else {}
    kept = {}                                   # id: [earlier pins], pins.json's first
    for fid, p in old.items():
        kept.setdefault(fid, []).append(p)
    for f in idx.get("firmwares", []) + idx.get("tried", []):
        kept.setdefault(f["id"], []).append({**f, "how": f.get("pin"), "ref": "published index"})
    ov_file = HERE / "overrides.json"
    override = json.loads(ov_file.read_text()).get("pin", {}) if ov_file.exists() else {}
    todo = []
    pins = {}
    for fw in cat["firmwares"]:
        entry = {"name": fw.get("name") or fw["id"], "repo": fw["repo"], "version": str(fw.get("version")), "pkg": fw.get("pkg"),
                 "sha256": fw.get("sha256"), "lic": fw.get("lic"), "tag": fw.get("tag")}
        o = override.get(fw["id"])
        if o and str(o.get("version")) == entry["version"] and SHA.fullmatch(o.get("commit") or ""):
            pins[fw["id"]] = {**entry, "commit": o["commit"], "how": "override", "ref": "overrides.json"}
            continue
        prev = next((p for p in kept.get(fw["id"], []) if str(p.get("version")) == entry["version"]
                     and p.get("repo") == entry["repo"] and SHA.fullmatch(p.get("commit") or "")), None)
        if prev:
            pins[fw["id"]] = {**entry, **{k: prev.get(k) for k in ("commit", "how", "ref")}}
        else:
            todo.append((fw, entry))

    def one(item):
        fw, entry = item
        try:
            return fw["id"], {**entry, **resolve(fw)}
        except Exception as e:                  # (a repository gone or private: recorded, not fatal)
            return fw["id"], {**entry, "commit": None, "how": "error", "ref": str(e)}

    with ThreadPoolExecutor(16) as ex:
        for fid, pin in ex.map(one, todo):
            pins[fid] = pin
    order = [fw["id"] for fw in cat["firmwares"]]
    PINS.write_text(json.dumps({k: pins[k] for k in order}, indent=1) + "\n")
    how = {}
    for p in pins.values():
        how[p["how"]] = how.get(p["how"], 0) + 1
    print("pins: %d (%s), %d resolved now" % (len(pins), ", ".join("%s %d" % kv for kv in sorted(how.items())),
                                             len(todo)))


if __name__ == "__main__":
    main()
