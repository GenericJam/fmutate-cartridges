"""pins.py: a firmware whose Hub version is unchanged keeps its pin (pins.json's, else the published index's), so a
HEAD pin doesn't move; a new version, or another repository, is resolved again. Resolving (git ls-remote) is faked."""
import json
import sys

import pytest

import pins
from conftest import sha


@pytest.fixture
def run_pins(tmp_path, monkeypatch, capsys):
    resolved = []

    def resolve(fw):
        resolved.append(fw["id"])
        return {"commit": sha(0xF00), "how": "head", "ref": "HEAD@today"}

    monkeypatch.setattr(pins, "resolve", resolve)
    here = tmp_path / "pipeline"
    here.mkdir()
    monkeypatch.setattr(pins, "HERE", here)
    monkeypatch.setattr(pins, "PINS", here / "pins.json")

    def run(catalog, index=None, old=None, overrides=None):
        (tmp_path / "catalog.json").write_text(json.dumps({"firmwares": catalog}))
        args = [str(tmp_path / "catalog.json")]
        if index is not None:
            (tmp_path / "index.json").write_text(json.dumps(index))
            args += ["--index", str(tmp_path / "index.json")]
        if old is not None:
            (here / "pins.json").write_text(json.dumps(old))
        if overrides is not None:
            (here / "overrides.json").write_text(json.dumps(overrides))
        monkeypatch.setattr(sys, "argv", ["pins.py", *args])
        pins.main()
        capsys.readouterr()
        return json.loads((here / "pins.json").read_text()), resolved
    return run


def hub(fid, version, repo=None):
    return {"id": fid, "name": fid, "repo": repo or f"someone/{fid}", "version": version, "lic": "MIT"}


def published(fid, version, commit, how="head", repo=None):
    return {"id": fid, "version": version, "repo": repo or f"someone/{fid}", "commit": commit, "pin": how}


def test_a_published_pin_is_kept_for_an_unchanged_version(run_pins):
    got, resolved = run_pins([hub("skiff", "1.0")], index={"firmwares": [published("skiff", "1.0", sha(1))]})
    assert resolved == []
    assert {k: got["skiff"][k] for k in ("commit", "how", "ref")} == \
        {"commit": sha(1), "how": "head", "ref": "published index"}


def test_a_tried_pin_is_kept_too(run_pins):
    got, resolved = run_pins([hub("skiff", "2.0")], index={"firmwares": [published("skiff", "1.0", sha(1))],
                                                           "tried": [published("skiff", "2.0", sha(2), "tag")]})
    assert resolved == [] and (got["skiff"]["commit"], got["skiff"]["how"]) == (sha(2), "tag")


@pytest.mark.parametrize("catalog", [hub("skiff", "1.1"), hub("skiff", "1.0", repo="fork/skiff")],
                         ids=["new version", "other repo"])
def test_a_changed_entry_is_resolved_again(run_pins, catalog):
    got, resolved = run_pins([catalog], index={"firmwares": [published("skiff", "1.0", sha(1))]})
    assert resolved == ["skiff"] and got["skiff"]["commit"] == sha(0xF00)


def test_pins_json_comes_before_the_index(run_pins):
    old = {"skiff": {"version": "1.0", "repo": "someone/skiff", "commit": sha(7), "how": "tag", "ref": "refs/tags/v1"}}
    got, resolved = run_pins([hub("skiff", "1.0")], index={"firmwares": [published("skiff", "1.0", sha(1))]}, old=old)
    assert resolved == [] and (got["skiff"]["commit"], got["skiff"]["ref"]) == (sha(7), "refs/tags/v1")


def test_an_override_for_this_version_wins(run_pins):
    ov = {"pin": {"skiff": {"version": "1.0", "commit": sha(3), "why": "release record"},
                  "sloop": {"version": "0.9", "commit": sha(4), "why": "an older version's"}}}
    got, resolved = run_pins([hub("skiff", "1.0"), hub("sloop", "1.0")],
                             index={"firmwares": [published("skiff", "1.0", sha(1))]}, overrides=ov)
    assert (got["skiff"]["commit"], got["skiff"]["how"]) == (sha(3), "override")
    assert resolved == ["sloop"] and got["sloop"]["how"] == "head"


def test_a_kept_pin_needs_a_commit(run_pins):
    got, resolved = run_pins([hub("skiff", "1.0")], index={"firmwares": [published("skiff", "1.0", None, "error")]})
    assert resolved == ["skiff"] and got["skiff"]["commit"] == sha(0xF00)
