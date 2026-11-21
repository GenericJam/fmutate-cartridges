"""publish.py: the index is strict JSON, a run's assets are split over releases, and it is signed and checked."""
import base64
import functools
import json
import math
import sys

import pytest

import publish
from conftest import android_job, beside, make_pin, sha, verify_report

TAG = "build-20261120-7"


def run_publish(monkeypatch, here, art, out, *args):
    monkeypatch.setattr(publish, "HERE", here)
    monkeypatch.setattr(sys, "argv", ["publish.py", "--artifacts", str(art), "--tag", TAG, "--out", str(out), *args])
    publish.main()
    return publish.strict_json((out / "index.json").read_text())


@pytest.mark.parametrize("const", ["NaN", "Infinity", "-Infinity"])
def test_strict_json_refuses_non_finite(const):
    with pytest.raises(SystemExit, match="isn't strict JSON"):
        publish.strict_json('{"parity": {"spectrum_corr": %s}}' % const)


def test_strict_json_parses_json():
    assert publish.strict_json('{"a": [1, 2.5, null, "NaN"]}') == {"a": [1, 2.5, None, "NaN"]}


def test_finite_makes_every_non_finite_number_null():
    x = {"a": math.nan, "b": [math.inf, 1.5, {"c": -math.inf, "d": 0}], "e": "NaN", "f": None}
    assert publish.finite(x) == {"a": None, "b": [None, 1.5, {"c": None, "d": 0}], "e": "NaN", "f": None}
    json.dumps(publish.finite(x), allow_nan=False)


def test_non_finite_metrics_are_null_in_a_strict_index(tmp_path, monkeypatch):
    # accepted by a person, with a check whose numbers are NaN / Infinity: published, the numbers as null
    here = beside(tmp_path / "pipeline", {"acc": make_pin("acc"), "nan": make_pin("nan")},
                  {"accept": {"acc": "checked by ear"}})
    android_job(tmp_path / "art", "acc", verify_report("acc", level_diff_db=math.nan, spectrum_corr=math.inf))
    android_job(tmp_path / "art", "nan", verify_report("nan", spectrum_corr=math.nan))
    index = run_publish(monkeypatch, here, tmp_path / "art", tmp_path / "dist", "--first")
    text = (tmp_path / "dist" / "index.json").read_text()
    assert "NaN" not in text and "Infinity" not in text
    [acc] = index["firmwares"]
    assert acc["id"] == "acc" and acc["parity"]["status"] == "accepted"
    assert acc["parity"]["level_diff_db"] is None and acc["parity"]["spectrum_corr"] is None
    # a NaN spectrum fails the check: tried, not published
    [nan] = index["tried"]
    assert nan["id"] == "nan" and nan["check"] == "diverges" and "spectrum" in nan["reason"]


@pytest.mark.parametrize("n_fw, releases", [(300, {TAG: 900}), (301, {TAG: 900, TAG + "-2": 3})])
def test_assets_split_over_releases(tmp_path, monkeypatch, n_fw, releases):
    ids = [f"fw{i:03d}" for i in range(n_fw)]
    here = beside(tmp_path / "pipeline", {i: make_pin(i) for i in ids})
    for i in ids:
        android_job(tmp_path / "art", i, verify_report(i))
    index = run_publish(monkeypatch, here, tmp_path / "art", tmp_path / "dist", "--first")
    held = {d.name: {f.name for f in d.iterdir()} for d in (tmp_path / "dist" / "assets").iterdir()}
    assert {k: len(v) for k, v in held.items()} == releases
    assert max(map(len, held.values())) <= publish.PER_RELEASE
    named = [a["url"] for f in index["firmwares"] for a in f["assets"].values()]
    assert len(named) == len(set(named)) == 3 * n_fw
    for url in named:
        release, name = url.split(f"/{publish.REPO}/releases/download/", 1)[1].split("/")
        assert name in held[release]


def test_index_and_assets_signed_and_verified(tmp_path, monkeypatch, throwaway_key):
    key, pub = throwaway_key
    here = beside(tmp_path / "pipeline", {"skiff": make_pin("skiff")})
    android_job(tmp_path / "art", "skiff", verify_report("skiff"))
    index = run_publish(monkeypatch, here, tmp_path / "art", tmp_path / "dist", "--first", "--key", str(key))
    ip, sig = tmp_path / "dist" / "index.json", (tmp_path / "dist" / "index.json.sig").read_text()
    assert publish.verified(ip, sig, pub=pub)
    for platform, a in index["firmwares"][0]["assets"].items():
        name = a["url"].rsplit("/", 1)[1]
        assert publish.verified(tmp_path / "dist" / "assets" / TAG / name, a["sig"], pub=pub), platform
    # one byte changed, or another key: refused
    tampered = tmp_path / "tampered.json"
    tampered.write_bytes(ip.read_bytes().replace(b'"skiff"', b'"skifF"', 1))
    assert not publish.verified(tampered, sig, pub=pub)
    assert not publish.verified(ip, sig)                     # (the published key didn't sign this)
    assert not publish.verified(ip, base64.b64encode(b"\0" * 64).decode(), pub=pub)


def test_previous_index_must_verify_against_the_published_key(tmp_path, throwaway_key):
    key, _ = throwaway_key
    prev = tmp_path / "index.json"
    prev.write_text(json.dumps({"schema": 1, "firmwares": [], "tried": []}))
    with pytest.raises(SystemExit, match="no previous index"):
        publish.load_prev(str(prev), first=False)
    (tmp_path / "index.json.sig").write_text(publish.sign(key, prev) + "\n")
    assert publish.verified(prev, (tmp_path / "index.json.sig").read_text(), pub=throwaway_key[1])
    with pytest.raises(SystemExit, match="doesn't verify against signing.pub.pem"):
        publish.load_prev(str(prev), first=False)


def test_failed_rebuild_keeps_the_published_entry(tmp_path, monkeypatch, throwaway_key):
    key, pub = throwaway_key
    monkeypatch.setattr(publish, "verified", functools.partial(publish.verified, pub=pub))
    pins = {"skiff": make_pin("skiff", commit=sha(5)), "sloop": make_pin("sloop", version="2.0", commit=sha(6))}
    here = beside(tmp_path / "pipeline", pins)
    published = {"id": "skiff", "version": "1.0", "repo": "someone/skiff", "commit": sha(5), "pin": "tag",
                 "assets": {"android-x86_64": {"url": "u", "sha256": "s", "size": 1, "sig": ""}}}
    older = {**published, "id": "sloop", "version": "1.0", "repo": "someone/sloop", "commit": sha(4)}
    prev = tmp_path / "prev" / "index.json"
    prev.parent.mkdir()
    prev.write_text(json.dumps({"schema": 1, "firmwares": [published, older], "tried": []}))
    (tmp_path / "prev" / "index.json.sig").write_text(publish.sign(key, prev))
    for fid in pins:
        android_job(tmp_path / "art", fid, verify_report(fid, spectrum_corr=0.2))
    index = run_publish(monkeypatch, here, tmp_path / "art", tmp_path / "dist", "--prev", str(prev))
    # skiff: the same pin failed this time, the published entry stays; sloop: a new version failed, tried, the older
    # version stays published
    assert [f["id"] for f in index["firmwares"]] == ["skiff", "sloop"]
    assert index["firmwares"][0] == published and index["firmwares"][1] == older
    assert [(t["id"], t["version"], t["commit"]) for t in index["tried"]] == [("sloop", "2.0", sha(6))]
