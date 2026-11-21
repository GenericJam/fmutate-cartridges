"""release.py: uploads what a release lacks, and the index goes out only when every asset it names is in its release
with the index's size and SHA-256. GitHub (`gh`) is a fake here: no network, no token."""
import hashlib
import json
import subprocess
import sys

import pytest

import publish
import release
from conftest import android_job, beside, make_pin, verify_report

REPO = "GenericJam/fmutate-cartridges"


class FakeGitHub:
    """releases {tag: {name: (size, sha256 or None)}}, as release.held reads them; `gh release view|create|upload`"""

    def __init__(self, releases=None):
        self.releases = releases or {}
        self.uploads = []

    def held(self, repo, tag):
        assert repo == REPO
        if tag not in self.releases:
            raise subprocess.CalledProcessError(1, ["gh", "api"], stderr="gh: Not Found (HTTP 404)")
        return dict(self.releases[tag])

    def gh(self, *args, check=True):
        assert args[0] == "release" and args[3:5] == ("-R", REPO), args
        cmd, tag = args[1], args[2]
        rc = 0
        if cmd == "view":
            rc = 0 if tag in self.releases else 1
        elif cmd == "create":
            self.releases[tag] = {}
        elif cmd == "upload":
            files = [f for f in args[5:] if f != "--clobber"]
            self.uploads.append((tag, files))
            for f in files:
                data = open(f, "rb").read()
                self.releases[tag][f.rsplit("/", 1)[1]] = (len(data), hashlib.sha256(data).hexdigest())
        else:
            raise AssertionError(args)
        if rc and check:
            raise subprocess.CalledProcessError(rc, ["gh", *args], stderr="")
        return subprocess.CompletedProcess(["gh", *args], rc, "", "")


@pytest.fixture
def github(monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setattr(release, "held", fake.held)
    monkeypatch.setattr(release, "gh", fake.gh)
    monkeypatch.setattr(release.time, "sleep", lambda s: None)
    return fake


def asset(tag, name, data):
    return {"url": f"https://github.com/{REPO}/releases/download/{tag}/{name}",
            "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "sig": ""}


def check(monkeypatch, tmp_path, index, assets_dir=None):
    ip = tmp_path / "index.json"
    ip.write_text(json.dumps(index))
    monkeypatch.setattr(sys, "argv", ["release.py", "--assets", str(assets_dir or tmp_path / "none"),
                                      "--index", str(ip), "--repo", REPO, "--notes", "n"])
    return release.main()


def two_release_index():
    """an index naming assets in an earlier run's release and in this run's"""
    a, b = b"aaa", b"bbbb"
    return {"firmwares": [{"id": "skiff", "assets": {"android-x86_64": asset("build-1", "a.so", a)}},
                          {"id": "sloop", "assets": {"android-x86_64": asset("build-2-2", "b.so", b)}}]}, a, b


def test_every_asset_present_passes(tmp_path, monkeypatch, github):
    index, a, b = two_release_index()
    github.releases = {"build-1": {"a.so": (len(a), hashlib.sha256(a).hexdigest())},
                       "build-2-2": {"b.so": (len(b), None)}}           # (no digest reported: size only)
    assert check(monkeypatch, tmp_path, index) is None


@pytest.mark.parametrize("held_b", [
    {},                                                         # not uploaded
    {"b.so": (3, None)},                                        # another size
    {"b.so": (4, hashlib.sha256(b"BBBB").hexdigest())},         # other bytes
], ids=["missing", "size", "sha256"])
def test_an_asset_not_as_the_index_names_it_fails(tmp_path, monkeypatch, github, held_b):
    index, a, _ = two_release_index()
    github.releases = {"build-1": {"a.so": (len(a), None)}, "build-2-2": held_b}
    with pytest.raises(SystemExit, match=r"\['build-2-2/b.so'\]"):
        check(monkeypatch, tmp_path, index)


def test_a_release_that_does_not_exist_fails(tmp_path, monkeypatch, github):
    index, a, _ = two_release_index()
    github.releases = {"build-1": {"a.so": (len(a), None)}}
    with pytest.raises(SystemExit, match="build-2-2/b.so"):
        check(monkeypatch, tmp_path, index)


def test_publish_then_release_uploads_every_split(tmp_path, monkeypatch, github):
    monkeypatch.setattr(publish, "PER_RELEASE", 4)
    ids = ["skiff", "sloop", "yawl"]
    here = beside(tmp_path / "pipeline", {i: make_pin(i) for i in ids})
    for i in ids:
        android_job(tmp_path / "art", i, verify_report(i))
    monkeypatch.setattr(publish, "HERE", here)
    dist = tmp_path / "dist"
    monkeypatch.setattr(sys, "argv", ["publish.py", "--artifacts", str(tmp_path / "art"), "--tag", "build-9",
                                      "--out", str(dist), "--first", "--repo", REPO])
    publish.main()
    # an earlier attempt of this run uploaded one file of build-9, and a wrong copy of another
    first = sorted((dist / "assets" / "build-9").iterdir())
    github.releases = {"build-9": {first[0].name: (first[0].stat().st_size, None), first[1].name: (1, None)}}
    index = publish.strict_json((dist / "index.json").read_text())
    check(monkeypatch, tmp_path, index, dist / "assets")
    assert {t: len(h) for t, h in github.releases.items()} == {"build-9": 4, "build-9-2": 4, "build-9-3": 1}
    uploaded = [f.rsplit("/", 1)[1] for _, files in github.uploads for f in files]
    assert first[0].name not in uploaded and first[1].name in uploaded and len(uploaded) == 8
    # the same index, one asset since lost: refused
    del github.releases["build-9-3"][next(iter(github.releases["build-9-3"]))]
    monkeypatch.setattr(release, "upload", lambda *a: None)
    with pytest.raises(SystemExit, match="build-9-3/"):
        check(monkeypatch, tmp_path, index, dist / "assets")
