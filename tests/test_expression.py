"""Expressive assets require pinned, current host proof; capabilities belong to individual signed assets."""
import functools
import json
import subprocess
import sys

import pytest

import build
import gate
import plan
import publish
import expression_verify
from expression_config import identity, required_checks
from conftest import ANDROID, android_job, beside, make_pin, sha, verify_report

CAPS = {"pitch_bend": True, "note_pressure": True}
NEUTRAL = [{"name": "host", "run": "true", "compare": ["out.wav"]}]
TAG = "build-expression"


def configure(here, fid="skiff", commit=sha(1)):
    manifest = here / "expression.json"
    entries = json.loads(manifest.read_text()) if manifest.exists() else {}
    entries[fid] = {"commit": commit, "caps": CAPS, "notes": [60, 67], "bend_semitones": 2, "neutral": NEUTRAL}
    manifest.write_text(json.dumps(entries))
    patch = here / "patches" / fid / "expression.patch"
    patch.parent.mkdir(parents=True, exist_ok=True)
    patch.write_bytes(b"expression patch for " + fid.encode())
    return identity(fid, commit, here)


def enhanced_job(art, fid, expressive):
    report = verify_report(fid)
    report["expression"] = {**expressive, "ok": True,
                            "checks": {name: {"ok": True} for name in required_checks(fid, {"neutral": NEUTRAL})}}
    job = android_job(art, fid, report)
    for result in (job / "results").glob("*.json"):
        value = json.loads(result.read_text())
        value["expression"] = expressive
        result.write_text(json.dumps(value))
    return job


def change_json(path, mutate):
    value = json.loads(path.read_text())
    mutate(value)
    path.write_text(json.dumps(value))


def publish_index(monkeypatch, here, art, out, *args):
    monkeypatch.setattr(publish, "HERE", here)
    monkeypatch.setattr(sys, "argv", ["publish.py", "--artifacts", str(art), "--tag", TAG, "--out", str(out), *args])
    publish.main()
    return publish.strict_json((out / "index.json").read_text())


def planned(monkeypatch, capsys, here, index, *args):
    monkeypatch.setattr(plan, "HERE", here)
    monkeypatch.setattr(sys, "argv", ["plan.py", "--index", str(index), *args])
    capsys.readouterr()
    plan.main()
    return json.loads(capsys.readouterr().out)


def previous(tmp_path, key, firmwares):
    path = tmp_path / "previous.json"
    path.write_text(json.dumps({"schema": 1, "firmwares": firmwares, "tried": []}))
    path.with_suffix(".json.sig").write_text(publish.sign(key, path))
    return path


@pytest.mark.parametrize("broken", ["missing", "failed", "stale", "caps", "numeric_caps", "checks", "incomplete",
                                     "neutral", "no_neutral", "wrong_id", "host", "platform", "pin"])
def test_expression_proof_fails_closed_even_with_accept_override(tmp_path, monkeypatch, broken):
    here = beside(tmp_path / "pipeline", {"skiff": make_pin("skiff")}, {"accept": {"skiff": "not a proof override"}})
    expressive = configure(here)
    job = enhanced_job(tmp_path / "art", "skiff", expressive)
    report = job / "verify" / "skiff.json"
    if broken == "missing":
        change_json(report, lambda v: v.pop("expression"))
    elif broken == "failed":
        change_json(report, lambda v: v["expression"].update(ok=False))
    elif broken == "stale":
        change_json(report, lambda v: v["expression"].update(revision="0" * 64))
    elif broken == "caps":
        change_json(report, lambda v: v["expression"]["caps"].update(unknown=True))
    elif broken == "numeric_caps":
        change_json(report, lambda v: v["expression"]["caps"].update(note_pressure=1))
    elif broken == "checks":
        change_json(report, lambda v: v["expression"]["checks"]["pressure_zero"].update(ok=False))
    elif broken == "incomplete":
        change_json(report, lambda v: v["expression"]["checks"].pop("pitch_bend"))
    elif broken == "neutral":
        change_json(report, lambda v: v["expression"]["checks"]["neutral_host"].update(ok=False))
    elif broken == "no_neutral":
        change_json(here / "expression.json", lambda v: v["skiff"].pop("neutral"))
    elif broken == "wrong_id":
        change_json(report, lambda v: v.update(id="caravel"))
    elif broken in {"host", "platform"}:
        target = "host" if broken == "host" else ANDROID[0]
        change_json(job / "results" / f"skiff.{target}.json", lambda v: v.pop("expression"))
    elif broken == "pin":
        change_json(here / "pins.json", lambda v: v["skiff"].update(commit=sha(2)))
    index = publish_index(monkeypatch, here, tmp_path / "art", tmp_path / "dist", "--first")
    assert index["firmwares"] == []
    assert index["tried"][0]["check"] == "expression-failed"
    assert not any((tmp_path / "dist" / "assets").iterdir())


@pytest.mark.parametrize("change, run, ok", [
    ("untested", "cat data.txt > out.txt", True),       # the patch leaves what the test renders alone
    ("tested", "cat data.txt > out.txt", False),        # the patch changes what the test renders
    ("untested", "false", False),                       # the firmware's own test fails
    ("untested", "true", False),                        # its output is never written
    ("stale", "true", False),                           # ... though an old one is left in the source tree
])
def test_neutral_check_requires_identical_outputs_with_the_patch_reversed(tmp_path, change, run, ok):
    pristine, source = tmp_path / "a", tmp_path / "b"
    for tree in (pristine, source):
        tree.mkdir()
        (tree / "data.txt").write_text("stock sound\n")
        (tree / "note.txt").write_text("stock comment\n")
    (source / ("data.txt" if change == "tested" else "note.txt")).write_text("expression\n")
    diff = subprocess.run(["diff", "-ru", "a", "b"], cwd=tmp_path, capture_output=True, text=True).stdout
    patch = tmp_path / "expression.patch"
    patch.write_text(diff)
    if change == "stale":
        (source / "out.txt").write_text("an earlier run's output\n")
    spec = {"neutral": [{"name": "render", "run": run, "compare": ["out.txt"]}]}
    checks = expression_verify.neutral("fw", spec, source=source, patch=patch)
    assert checks["neutral_render"]["ok"] is ok


@pytest.mark.parametrize("rebuild, old_assets", [("built", "mixed"), ("failed", "mixed"), ("failed", "all_caps")])
def test_a_firmware_taken_out_of_the_manifest_is_rebuilt_and_loses_its_caps(tmp_path, monkeypatch, capsys,
                                                                            throwaway_key, rebuild, old_assets):
    key, pub = throwaway_key
    monkeypatch.setattr(publish, "verified", functools.partial(publish.verified, pub=pub))
    here = beside(tmp_path / "pipeline", {"skiff": make_pin("skiff")})   # (no expression.json entry any more)
    with_caps = {"url": "old-ios", "sha256": "a" * 64, "size": 7, "sig": "s", "caps": CAPS}
    plain = {"url": "old-sim", "sha256": "b" * 64, "size": 7, "sig": "s"}
    assets = {"ios-device": with_caps, **({"ios-sim": plain} if old_assets == "mixed" else {})}
    old = {"id": "skiff", **make_pin("skiff"), "expression": {"revision": "0" * 64, "caps": CAPS}, "assets": assets}
    prev = previous(tmp_path, key, [old])
    assert planned(monkeypatch, capsys, here, prev) == ["skiff"]

    android_job(tmp_path / "art", "skiff", verify_report("skiff"), platforms=ANDROID if rebuild == "built" else ())
    index = publish_index(monkeypatch, here, tmp_path / "art", tmp_path / "dist", "--prev", str(prev),
                          "--key", str(key))
    if old_assets == "all_caps":
        # nothing left to download: not published, listed as tried (and not planned again at this pin)
        assert index["firmwares"] == [] and index["tried"][0]["id"] == "skiff"
        return
    entry = index["firmwares"][0]
    assert "expression" not in entry
    assert "ios-device" not in entry["assets"] and entry["assets"]["ios-sim"] == plain
    assert not any("caps" in asset for asset in entry["assets"].values())


@pytest.mark.parametrize("broken", ["missing", "failed", "stale", "host", "platform"])
def test_ios_gate_cannot_override_expression_failure(tmp_path, monkeypatch, capsys, broken):
    here = beside(tmp_path / "pipeline", {"skiff": make_pin("skiff")}, {"accept": {"skiff": "parity only"}})
    expressive = configure(here)
    job = enhanced_job(tmp_path / "art", "skiff", expressive)
    report = job / "verify" / "skiff.json"
    if broken == "missing":
        change_json(report, lambda v: v.pop("expression"))
    elif broken == "failed":
        change_json(report, lambda v: v["expression"].update(ok=False))
    elif broken == "stale":
        change_json(report, lambda v: v["expression"].update(revision="0" * 64))
    else:
        target = "host" if broken == "host" else ANDROID[0]
        change_json(job / "results" / f"skiff.{target}.json", lambda v: v.pop("expression"))
    monkeypatch.setattr(gate, "HERE", here)
    monkeypatch.setattr(sys, "argv", ["gate.py", "--artifacts", str(tmp_path / "art")])
    gate.main()
    output, diagnostic = capsys.readouterr()
    assert json.loads(output) == []
    assert "expression" in diagnostic


@pytest.mark.parametrize("old_caps", [None, {"pitch_bend": True, "note_pressure": False}])
def test_signed_partial_platform_merge_keeps_old_asset_capabilities(tmp_path, monkeypatch, throwaway_key, old_caps):
    key, pub = throwaway_key
    monkeypatch.setattr(publish, "verified", functools.partial(publish.verified, pub=pub))
    here = beside(tmp_path / "pipeline", {"skiff": make_pin("skiff")})
    expressive = configure(here)
    old_asset = {"url": "old-ios", "sha256": "old-sha", "size": 42, "sig": "old-signature"}
    if old_caps is not None:
        old_asset["caps"] = old_caps
    old = {"id": "skiff", **make_pin("skiff"), "assets": {"ios-device": old_asset}}
    prev = previous(tmp_path, key, [old])
    enhanced_job(tmp_path / "art", "skiff", expressive)
    index = publish_index(monkeypatch, here, tmp_path / "art", tmp_path / "dist", "--prev", str(prev), "--key", str(key))
    entry = index["firmwares"][0]
    assert entry["assets"]["ios-device"] == old_asset
    assert "caps" not in entry
    assert entry["expression"] == expressive
    for platform in ANDROID:
        assert entry["assets"][platform]["caps"] == CAPS
    ip = tmp_path / "dist" / "index.json"
    assert publish.verified(ip, ip.with_suffix(".json.sig").read_text())
    tampered = tmp_path / "tampered.json"
    tampered.write_text(ip.read_text().replace('"note_pressure": true', '"note_pressure": false'))
    assert not publish.verified(tampered, ip.with_suffix(".json.sig").read_text())


@pytest.mark.parametrize("spectrum, accepted, passed", [(0.97, False, ["skiff"]), (0.2, False, []),
                                                      (0.2, True, ["skiff"])])
def test_valid_expression_proof_does_not_replace_parity_gate(tmp_path, monkeypatch, capsys, spectrum, accepted, passed):
    here = beside(tmp_path / "pipeline", {"skiff": make_pin("skiff")},
                  {"accept": {"skiff": "reviewed parity difference"}} if accepted else {})
    expressive = configure(here)
    job = enhanced_job(tmp_path / "art", "skiff", expressive)
    change_json(job / "verify" / "skiff.json", lambda v: v["compare"].update(spectrum_corr=spectrum))
    monkeypatch.setattr(gate, "HERE", here)
    monkeypatch.setattr(sys, "argv", ["gate.py", "--artifacts", str(tmp_path / "art")])
    gate.main()
    assert json.loads(capsys.readouterr().out) == passed


def test_unconfigured_firmware_cannot_claim_job_capabilities(tmp_path, monkeypatch):
    here = beside(tmp_path / "pipeline", {"plain": make_pin("plain")})
    enhanced_job(tmp_path / "art", "plain", {"revision": "0" * 64, "caps": CAPS})
    index = publish_index(monkeypatch, here, tmp_path / "art", tmp_path / "dist", "--first")
    entry = index["firmwares"][0]
    assert "expression" not in entry
    assert all("caps" not in asset for asset in entry["assets"].values())


@pytest.mark.parametrize("change", ["patch", "manifest"])
def test_same_version_revision_change_rebuilds_only_affected_firmware(tmp_path, monkeypatch, capsys, change):
    pins = {fid: make_pin(fid) for fid in ("skiff", "caravel", "plain")}
    here = beside(tmp_path / "pipeline", pins)
    configured = {fid: configure(here, fid) for fid in ("skiff", "caravel")}
    entries = [{"id": fid, **pin, **({"expression": configured[fid]} if fid in configured else {})}
               for fid, pin in pins.items()]
    index = tmp_path / "index.json"
    index.write_text(json.dumps({"firmwares": entries, "tried": []}))
    assert planned(monkeypatch, capsys, here, index) == []
    if change == "patch":
        (here / "patches" / "skiff" / "expression.patch").write_bytes(b"changed expression behavior")
    else:
        change_json(here / "expression.json", lambda v: v["skiff"].update(bend_semitones=3))
    assert planned(monkeypatch, capsys, here, index) == ["skiff"]


def test_failed_same_pin_attempt_keeps_published_assets_and_is_not_repeated(tmp_path, monkeypatch, capsys, throwaway_key):
    key, pub = throwaway_key
    monkeypatch.setattr(publish, "verified", functools.partial(publish.verified, pub=pub))
    here = beside(tmp_path / "pipeline", {"skiff": make_pin("skiff")})
    expressive = configure(here)
    old = {"id": "skiff", **make_pin("skiff"), "assets": {"ios-device": {"url": "old", "size": 7}}}
    prev = previous(tmp_path, key, [old])
    android_job(tmp_path / "art", "skiff", verify_report("skiff"))
    out = tmp_path / "dist"
    index = publish_index(monkeypatch, here, tmp_path / "art", out, "--prev", str(prev))
    assert index["firmwares"] == [old]
    assert index["tried"][0]["expression"] == expressive
    assert planned(monkeypatch, capsys, here, out / "index.json") == []
    (here / "patches" / "skiff" / "expression.patch").write_bytes(b"fixed expression")
    assert planned(monkeypatch, capsys, here, out / "index.json") == ["skiff"]


def test_incompatible_source_pin_is_excluded_without_rebuilding_unrelated_entries(tmp_path, monkeypatch, capsys):
    here = beside(tmp_path / "pipeline", {"skiff": make_pin("skiff", commit=sha(2)), "plain": make_pin("plain")})
    configure(here)
    index = tmp_path / "index.json"
    index.write_text(json.dumps({"firmwares": [{"id": "plain", **make_pin("plain")}]}))
    assert planned(monkeypatch, capsys, here, index) == []
    assert planned(monkeypatch, capsys, here, index, "--all") == ["plain"]


@pytest.mark.parametrize("source_pin", [None, sha(2)])
def test_build_rejects_unknown_or_incompatible_fetched_source(tmp_path, monkeypatch, source_pin):
    here = beside(tmp_path / "pipeline", {"skiff": make_pin("skiff")})
    configure(here)
    src = tmp_path / "src"
    (src / "skiff").mkdir(parents=True)
    if source_pin is not None:
        (src / "skiff" / ".pin").write_text(source_pin + " deps:abc\n")
    monkeypatch.setattr(build, "HERE", here)
    monkeypatch.setattr(build, "SRC", src)
    monkeypatch.setattr(build, "PINS", {"skiff": make_pin("skiff")})
    result, _ = build.build("skiff", "host")
    assert result["ok"] is False and result["stage"] == "expression"
    assert "expression" not in result
