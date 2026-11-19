#!/usr/bin/env python3
"""Upload a run's cartridges to its GitHub releases and check they are all there before the index goes out.

    python3 pipeline/release.py --assets DIST/assets --index DIST/index.json --repo OWNER/NAME --notes TEXT [--extra FILE]

DIST/assets/<tag>/ holds one release's files (publish.py splits a run's assets over releases: GitHub keeps at most
1000 assets in one). Each release is created if missing; files it doesn't hold yet (by name, size and SHA-256) are
uploaded, a few rounds, waiting out the token's rate limit when it runs out (a run that rebuilds everything uploads
hundreds). Then every asset of these releases that the index names must be in its release with the index's size and
SHA-256, or this exits non-zero and the index isn't published. Needs `gh` with GH_TOKEN (contents: write).
"""
import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROUNDS = 5
MAX_WAIT_S = 65 * 60


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


def wait_for_rate_limit():
    r = gh("api", "rate_limit", "-q", ".resources.core | [.remaining, .reset] | @tsv", check=False)
    if r.returncode or not r.stdout.strip():
        time.sleep(60)
        return
    remaining, reset = (int(x) for x in r.stdout.split())
    if remaining > 50:
        time.sleep(30)
        return
    wait = max(0, reset - int(time.time())) + 10
    if wait > MAX_WAIT_S:
        raise SystemExit(f"release: rate limit resets in {wait} s, past this job's budget")
    print(f"release: rate limit ({remaining} left), waiting {wait} s", flush=True)
    time.sleep(wait)


def upload(repo, tag, files, notes):
    for _ in range(ROUNDS):
        try:
            if gh("release", "view", tag, "-R", repo, check=False).returncode:
                gh("release", "create", tag, "-R", repo, "--title", tag, "--notes", notes)
            have = held(repo, tag)
        except subprocess.CalledProcessError as e:
            print(f"release {tag}: {' '.join(e.cmd[1:3])} failed: {e.stderr.strip()[-300:]}", flush=True)
            wait_for_rate_limit()
            continue
        todo = [f for f in files if f.name not in have or not same(f, have[f.name])]
        if not todo:
            return
        print(f"release {tag}: uploading {len(todo)} of {len(files)}", flush=True)
        for i in range(0, len(todo), 50):
            r = gh("release", "upload", tag, "-R", repo, "--clobber", *map(str, todo[i:i + 50]), check=False)
            if r.returncode:
                print(f"release {tag}: upload failed: {r.stderr.strip()[-300:]}", flush=True)
                wait_for_rate_limit()
                break


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--assets", required=True)
    ap.add_argument("--index", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--notes", required=True)
    ap.add_argument("--extra", action="append", default=[], help="a file every release also carries (pins.json)")
    a = ap.parse_args()
    dirs = sorted(d for d in Path(a.assets).iterdir() if d.is_dir()) if Path(a.assets).is_dir() else []
    if not dirs:
        print("release: no new assets")
        return
    for d in dirs:
        upload(a.repo, d.name, sorted(d.iterdir()) + [Path(x) for x in a.extra], a.notes)
    index = json.loads(Path(a.index).read_text())
    have = {d.name: held(a.repo, d.name) for d in dirs}
    missing = []
    for f in index["firmwares"]:
        for platform, asset in f["assets"].items():
            tag, name = asset["url"].split("/releases/download/", 1)[1].split("/", 1)
            if tag not in have:
                continue                        # (an earlier run's release, checked when that run published)
            got = have[tag].get(name)
            if not got or got[0] != asset["size"] or got[1] not in (None, asset["sha256"]):
                missing.append(f"{tag}/{name}")
    if missing:
        raise SystemExit(f"release: {len(missing)} assets the index names aren't in their release: {missing[:10]}")
    print(f"release: {sum(len(h) for h in have.values())} assets in {len(have)} release(s), all the index names")


if __name__ == "__main__":
    sys.exit(main())
