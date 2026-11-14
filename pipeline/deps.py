#!/usr/bin/env python3
"""Fetch a firmware's pinned build dependencies (pipeline/deps/<id>.json) into its fetched tree: what its own build
would otherwise fetch unpinned at build time (a `git clone` of a branch, a download), or what its repository needs but
doesn't hold. Run by fetch.sh after the firmware's own tree is in place; nothing here runs a firmware's scripts.

    python3 pipeline/deps.py <id> <tree>      fetch the deps into <tree>
    python3 pipeline/deps.py --check          validate every deps file (CI's plan job)
    python3 pipeline/deps.py --digest <id>    a digest of the deps file ("" without one), so fetch.sh refetches on change

pipeline/deps/<id>.json: {"deps": [DEP, ...]}, each DEP one of
  {"repo": "owner/name", "commit": "<40 hex>", "path": "dir/in/the/tree", "why": "..."}
      that GitHub repository at that commit (one shallow fetch, submodules too, .git removed) at <tree>/<path>,
      replacing what is there (a dangling symlink, an empty submodule directory). Optional "apply": patch files
      (paths relative to <tree>), applied in order inside <path> with `git apply` (a firmware that ships as patches
      over another project's tree, as Kitten Synths over SLOOP). Optional "env": a variable name the build sets to
      <tree>/<path> (a firmware's build that takes the dependency's directory from its environment: fm1-amy's
      AMY_DIR).
  {"catalog": "<id>", "why": "..."}
      another Hub firmware, fetched at its own pin into the work dir's src/ (recipes read it there).
Commits are full SHA-1s and paths stay inside the tree, so a deps file can't name a moving branch or write outside.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEPS = HERE / "deps"
SHA = re.compile(r"[0-9a-f]{40}")
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
ENV = re.compile(r"[A-Z_][A-Z0-9_]*")


def load(fid):
    p = DEPS / f"{fid}.json"
    return json.loads(p.read_text()).get("deps", []) if p.exists() else []


def inside(tree, rel):
    """<tree>/<rel>, refused unless it stays inside the tree"""
    if not rel or os.path.isabs(rel) or ".." in Path(rel).parts:
        raise SystemExit(f"deps: path {rel!r} must be relative, inside the tree")
    return tree / rel


def validate(fid, deps):
    pins = json.loads((HERE / "pins.json").read_text())
    for d in deps:
        if "catalog" in d:
            if d["catalog"] not in pins:
                raise SystemExit(f"deps/{fid}.json: catalog dependency {d['catalog']!r} is not a pinned firmware")
            continue
        if not REPO.fullmatch(d.get("repo", "")) or not SHA.fullmatch(d.get("commit", "")):
            raise SystemExit(f"deps/{fid}.json: {d} needs repo owner/name and a full 40-hex commit")
        inside(Path("/t"), d.get("path"))
        if "env" in d and not ENV.fullmatch(d["env"]):
            raise SystemExit(f"deps/{fid}.json: env {d['env']!r} is not a variable name")
        for a in d.get("apply", []):
            inside(Path("/t"), a)


def env(fid, tree):
    """the variables the build sets for the deps that name one ("env": the dependency's directory)"""
    return {d["env"]: str(inside(Path(tree).resolve(), d["path"])) for d in load(fid) if "env" in d}


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})


def fetch(fid, tree):
    tree = Path(tree).resolve()
    deps = load(fid)
    validate(fid, deps)
    for d in deps:
        if "catalog" in d:
            subprocess.run(["sh", str(HERE / "fetch.sh"), d["catalog"]], check=True)
            continue
        dst = inside(tree, d["path"])
        if dst.is_symlink() or dst.is_file():
            dst.unlink()
        elif dst.exists():
            shutil.rmtree(dst)
        dst.mkdir(parents=True)
        git("init", "-q", cwd=dst)
        git("remote", "add", "origin", f"https://github.com/{d['repo']}.git", cwd=dst)
        git("fetch", "-q", "--depth", "1", "origin", d["commit"], cwd=dst)
        git("-c", "advice.detachedHead=false", "checkout", "-q", "FETCH_HEAD", cwd=dst)
        git("submodule", "-q", "update", "--init", "--recursive", "--depth", "1", cwd=dst)
        for a in d.get("apply", []):
            git("apply", "--whitespace=nowarn", str(inside(tree, a)), cwd=dst)
        for g in list(dst.rglob(".git")):
            shutil.rmtree(g, ignore_errors=True) if g.is_dir() else g.unlink(missing_ok=True)
        print(f"{fid}: dep {d['repo']}@{d['commit'][:12]} -> {d['path']}")


def main():
    a = sys.argv[1:]
    if a == ["--check"]:
        for p in sorted(DEPS.glob("*.json")):
            validate(p.stem, load(p.stem))
        print(f"deps: {len(list(DEPS.glob('*.json')))} files ok")
    elif len(a) == 2 and a[0] == "--digest":
        p = DEPS / f"{a[1]}.json"
        print(hashlib.sha256(p.read_bytes()).hexdigest()[:16] if p.exists() else "")
    elif len(a) == 2:
        fetch(a[0], a[1])
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
