"""Tests of the pipeline's own logic: no builds, no network, no GitHub, no secrets (signatures use a key made here).

pipeline/ goes on the import path; the helpers write what the scripts read beside themselves (pins.json,
overrides.json) and what the build jobs upload (cart-android-<id>/...), and make a throwaway Ed25519 key."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

PIPELINE = Path(__file__).resolve().parent.parent / "pipeline"
sys.path.insert(0, str(PIPELINE))

ANDROID = ("android-arm64-v8a", "android-x86_64", "android-armeabi-v7a")


def sha(n):
    """a 40-hex commit"""
    return f"{n:040x}"


def make_pin(fid, version="1.0", commit=sha(1), repo=None, how="tag"):
    return {"name": fid, "repo": repo or f"someone/{fid}", "version": version, "pkg": f"fw/{fid}.fwsc",
            "sha256": "0" * 64, "lic": "MIT", "tag": None, "commit": commit, "how": how, "ref": "refs/tags/v" + version}


def beside(d, pins, overrides=None):
    """d as the directory a pipeline script reads pins.json and overrides.json from"""
    d.mkdir(parents=True, exist_ok=True)
    (d / "pins.json").write_text(json.dumps(pins))
    if overrides is not None:
        (d / "overrides.json").write_text(json.dumps(overrides))
    return d


def verify_report(fid, drop=(), **compare):
    """verify.py's report for a run where native and emulator agree (report.verdict: match), compare overridden;
    `drop` names compare keys to leave out"""
    run = {"chord": {"peak": 5000, "rms_db": -20.0}, "play": {"peak": 5000, "rms_db": -22.0}, "lit_screen": True}
    c = {"window": "chord", "level_diff_db": 0.5, "spectrum_corr": 0.97,
         "screen_diff_boot": 1.0, "screen_diff_chord": 1.0, "screen_diff_play": 1.0, **compare}
    for k in drop:
        c.pop(k)
    return {"id": fid, "native": run, "emu": run, "compare": c}


def android_job(art, fid, verify=None, platforms=ANDROID):
    """an android build job's artifact: host + ABI results, one cartridge per ABI (distinct bytes), verify/<id>.json
    (written as verify.py's json would be read back: NaN allowed here, so a NaN report can be made)"""
    job = art / f"cart-android-{fid}"
    (job / "results").mkdir(parents=True)
    (job / "results" / f"{fid}.host.json").write_text(
        json.dumps({"id": fid, "target": "host", "ok": True, "recipe": {"recipe": "plain"}}))
    for t in platforms:
        (job / "results" / f"{fid}.{t}.json").write_text(json.dumps({"id": fid, "target": t, "ok": True}))
        lib = job / "out" / t / f"libcart_{fid}.so"
        lib.parent.mkdir(parents=True)
        lib.write_bytes(f"{fid} {t}".encode())
    if verify is not None:
        (job / "verify").mkdir()
        (job / "verify" / f"{fid}.json").write_text(json.dumps(verify))
    return job


@pytest.fixture(scope="session")
def throwaway_key(tmp_path_factory):
    """(private, public) PEM paths of an Ed25519 key made for this test session, never the real signing key"""
    d = tmp_path_factory.mktemp("key")
    key, pub = d / "throwaway.pem", d / "throwaway.pub.pem"
    subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(key)], check=True, capture_output=True)
    subprocess.run(["openssl", "pkey", "-in", str(key), "-pubout", "-out", str(pub)], check=True, capture_output=True)
    assert pub.read_text() != (PIPELINE / "signing.pub.pem").read_text()
    return key, pub
