#!/usr/bin/env python3
"""Upload a run's cartridges to its GitHub releases and check they are all there before the index goes out.

    python3 pipeline/release.py --assets DIST/assets --index DIST/index.json --repo OWNER/NAME --notes TEXT [--extra FILE]

DIST/assets/<tag>/ holds one release's files (publish.py splits a run's assets over releases: GitHub keeps at most
1000 assets in one). Each release is created if missing; files it doesn't hold yet (by name, size and SHA-256) are
uploaded, paced and backing off whenever GitHub's rate limits stop it (a run that rebuilds everything uploads
hundreds). Then every asset the index names (earlier runs' releases too) must be in its release with the index's size
and SHA-256, or this exits non-zero and the index isn't published. Needs `gh` with GH_TOKEN (contents: write).
"""
import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

BATCH = 20                  # files per `gh release upload`
PACE_S = 25                 # between batches: well under GitHub's 80 content-creating requests a minute
BUDGET_S = 200 * 60         # (the publish job's timeout is 240 min)
DEADLINE = time.monotonic() + BUDGET_S


def gh(*args, check=True):
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=check)


def held(repo, tag):
    """{name: (size, sha256 or None)} of the release's uploaded assets"""
    rid = gh("api", f"repos/{repo}/releases/tags/{tag}", "-q", ".id").stdout.strip()
    out = gh("api", "--paginate", f"repos/{repo}/releases/{rid}/assets?per_page=100",
             "-q", '.[] | select(.state == "uploaded") | [.name, .size, (.digest // "")] | @tsv').stdout
    have = {}
    for line in out.splitlines():
        name, size, digest = line.split("\t")
        have[name] = (int(size), digest[len("sha256:"):] if digest.startswith("sha256:") else None)
    return have


def same(path, got):
    size, sha = got
    return size == path.stat().st_size and sha in (None, hashlib.sha256(path.read_bytes()).hexdigest())


def backoff(tries, why):
    """after a failed call: until the token's hourly limit resets when it is spent, else 1, 2, 4 ... 16 min (GitHub's
    secondary limits, on content creation among others, don't say when they lift)"""
    wait = min(60 * 2 ** tries, 16 * 60)
    r = gh("api", "rate_limit", "-q", ".resources.core | [.remaining, .reset] | @tsv", check=False)
    if not r.returncode and r.stdout.strip():
        remaining, reset = (int(x) for x in r.stdout.split())
        if remaining <= 50:
            wait = max(wait, reset - int(time.time()) + 10)
    if time.monotonic() + wait > DEADLINE:
        raise SystemExit(f"release: out of time waiting for GitHub ({why})")
    print(f"release: {why}; waiting {wait} s", flush=True)
    time.sleep(wait)


def upload(repo, tag, files, notes):
    tries = 0
    while True:
        try:
            if gh("release", "view", tag, "-R", repo, check=False).returncode:
                gh("release", "create", tag, "-R", repo, "--title", tag, "--notes", notes)
            have = held(repo, tag)
        except subprocess.CalledProcessError as e:
            backoff(tries, f"{tag}: {' '.join(e.cmd[1:3])} failed: {e.stderr.strip()[:300]}")
            tries += 1
            continue
        todo = [f for f in files if f.name not in have or not same(f, have[f.name])]
        if not todo:
            return
        print(f"release {tag}: uploading {len(todo)} of {len(files)}", flush=True)
        for i in range(0, len(todo), BATCH):
            r = gh("release", "upload", tag, "-R", repo, "--clobber", *map(str, todo[i:i + BATCH]), check=False)
            if r.returncode:
                backoff(tries, f"{tag}: upload failed: {r.stderr.strip()[:300]}")
                tries += 1
                break
            tries = 0
            time.sleep(PACE_S)


def listed(repo, tag):
    """held(), retried; {} for a release that doesn't exist"""
    for tries in range(4):
        try:
            return held(repo, tag)
        except subprocess.CalledProcessError as e:
            if "404" in e.stderr:
                return {}
            backoff(tries, f"{tag}: listing failed: {e.stderr.strip()[:300]}")
    return held(repo, tag)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--assets", required=True)
    ap.add_argument("--index", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--notes", required=True)
    ap.add_argument("--extra", action="append", default=[], help="a file every release also carries (pins.json)")
    a = ap.parse_args()
    dirs = sorted(d for d in Path(a.assets).iterdir() if d.is_dir()) if Path(a.assets).is_dir() else []
    for d in dirs:
        upload(a.repo, d.name, sorted(d.iterdir()) + [Path(x) for x in a.extra], a.notes)
    index = json.loads(Path(a.index).read_text())
    have = {}
    missing = []
    for f in index["firmwares"]:                # (earlier runs' releases too: the index must not name a lost file)
        for asset in f["assets"].values():
            tag, name = asset["url"].split(f"/{a.repo}/releases/download/", 1)[1].split("/", 1)
            if tag not in have:
                have[tag] = listed(a.repo, tag)
            got = have[tag].get(name)
            if not got or got[0] != asset["size"] or got[1] not in (None, asset["sha256"]):
                missing.append(f"{tag}/{name}")
    if missing:
        raise SystemExit(f"release: {len(missing)} assets the index names aren't in their release: {missing[:10]}")
    print(f"release: {len(dirs)} release(s) this run; all {sum(len(f['assets']) for f in index['firmwares'])} assets "
          f"the index names are in their {len(have)} release(s)")


if __name__ == "__main__":
    sys.exit(main())
