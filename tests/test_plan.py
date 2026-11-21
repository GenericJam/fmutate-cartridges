"""plan.py: a pin already published or already tried isn't built again; anything else pinned is."""
import json
import sys

import pytest

import plan
from conftest import beside, make_pin, sha


def run_plan(monkeypatch, capsys, here, *args):
    monkeypatch.setattr(plan, "HERE", here)
    monkeypatch.setattr(sys, "argv", ["plan.py", *args])
    plan.main()
    return json.loads(capsys.readouterr().out)


def entry(fid, version, commit):
    return {"id": fid, "version": version, "commit": commit, "repo": f"someone/{fid}", "pin": "tag"}


@pytest.fixture
def index(tmp_path):
    def write(firmwares=(), tried=()):
        p = tmp_path / "index.json"
        p.write_text(json.dumps({"schema": 1, "firmwares": list(firmwares), "tried": list(tried)}))
        return str(p)
    return write


def test_a_pin_tried_while_an_older_version_is_published_is_not_rebuilt(tmp_path, monkeypatch, capsys, index):
    here = beside(tmp_path / "pipeline", {"skiff": make_pin("skiff", "2.0", sha(2))})
    idx = index(firmwares=[entry("skiff", "1.0", sha(1))], tried=[entry("skiff", "2.0", sha(2))])
    assert run_plan(monkeypatch, capsys, here, "--index", idx) == []


@pytest.mark.parametrize("published, tried, build", [
    ([entry("skiff", "1.0", sha(1))], [], True),                        # a new version
    ([entry("skiff", "2.0", sha(2))], [], False),                       # this pin published
    ([], [entry("skiff", "2.0", sha(2))], False),                       # this pin tried
    ([], [entry("skiff", "2.0", sha(9))], True),                        # this version tried at another commit
    ([entry("skiff", "1.0", sha(1))], [entry("skiff", "1.5", sha(3))], True),   # an older version tried
    ([entry("sloop", "2.0", sha(2))], [], True),                        # another firmware's entry
])
def test_built_unless_this_pin_is_published_or_tried(tmp_path, monkeypatch, capsys, index, published, tried, build):
    here = beside(tmp_path / "pipeline", {"skiff": make_pin("skiff", "2.0", sha(2))})
    assert run_plan(monkeypatch, capsys, here, "--index", index(published, tried)) == (["skiff"] if build else [])


def test_no_published_index_builds_everything_but_skipped(tmp_path, monkeypatch, capsys):
    pins = {"skiff": make_pin("skiff"), "sloop": make_pin("sloop"), "yawl": make_pin("yawl")}
    here = beside(tmp_path / "pipeline", pins, {"skip": {"sloop": "licence question"}})
    assert run_plan(monkeypatch, capsys, here, "--index", str(tmp_path / "absent.json")) == ["skiff", "yawl"]


def test_only_well_formed_pins_are_planned(tmp_path, monkeypatch, capsys):
    pins = {"skiff": make_pin("skiff"), "Bad Id": make_pin("x"), "nosha": make_pin("nosha", commit=None),
            "badrepo": make_pin("badrepo", repo="not a repo; rm -rf /")}
    here = beside(tmp_path / "pipeline", pins)
    assert run_plan(monkeypatch, capsys, here, "--all") == ["skiff"]


def test_named_ids_are_limited_to_pinned_firmwares(tmp_path, monkeypatch, capsys):
    here = beside(tmp_path / "pipeline", {"skiff": make_pin("skiff"), "sloop": make_pin("sloop")})
    # (named: once each, in the order named; anything not pinned dropped)
    assert run_plan(monkeypatch, capsys, here, "sloop", "../etc", "skiff", "sloop") == ["sloop", "skiff"]
