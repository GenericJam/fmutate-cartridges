"""The parity gate on verify.py's reports (report.verdict, which gate.py and publish.py apply): a check without a
number (NaN, null, missing, infinite) fails it; verify.py's own comparison writes no NaN."""
import json
import math
import sys
import warnings

import numpy as np
import pytest

import gate
import verify
from report import verdict
from conftest import android_job, beside, make_pin, verify_report


def test_agreeing_runs_match():
    assert verdict(verify_report("skiff")) == ("match", [])


@pytest.mark.parametrize("compare, drop", [
    ({"spectrum_corr": math.nan}, ()),
    ({"spectrum_corr": None}, ()),
    ({"spectrum_corr": math.inf}, ()),
    ({"spectrum_corr": "0.97"}, ()),
    ({}, ("spectrum_corr",)),
], ids=["nan", "null", "inf", "string", "missing"])
def test_no_spectrum_number_fails(compare, drop):
    check, why = verdict(verify_report("skiff", drop=drop, **compare))
    assert check == "diverges" and any(w.startswith("spectrum r=") for w in why)


@pytest.mark.parametrize("compare, drop, reason", [
    ({"level_diff_db": math.nan}, (), "level"),
    ({}, ("level_diff_db",), "level"),
    ({"screen_diff_chord": math.nan}, (), "screen chord"),
])
def test_other_non_finite_measures_fail(compare, drop, reason):
    check, why = verdict(verify_report("skiff", drop=drop, **compare))
    assert check == "diverges" and any(w.startswith(reason) for w in why)


def run(peak, rms_db=-20.0):
    lvl = {"peak": peak, "rms_db": rms_db}
    return {"chord": lvl, "play": {"peak": peak, "rms_db": rms_db - 5}}


def test_compare_writes_no_nan_for_a_flat_spectrum():
    # a flat band spectrum has no correlation (np.corrcoef: NaN): null in the report, which then fails the gate
    flat = {"chord": np.full(25, -30.0), "play": np.full(25, -30.0)}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        c = verify.compare((run(5000), None, {}, flat), (run(5000), None, {}, flat))
    assert c["window"] == "chord" and "spectrum_corr" in c and c["spectrum_corr"] is None
    json.dumps(c, allow_nan=False)
    rep = verify_report("skiff", **c)
    assert verdict(rep)[0] == "diverges"


def test_compare_measures_a_spectrum_only_when_both_sound():
    shaped = {"chord": np.linspace(-60, 0, 25), "play": np.linspace(-60, 0, 25)}
    c = verify.compare((run(5000), None, {}, shaped), (run(5000), None, {}, shaped))
    assert c["spectrum_corr"] == 1.0 and c["level_diff_db"] == 0.0
    quiet = verify.compare((run(5000), None, {}, shaped), (run(10), None, {}, shaped))
    assert "spectrum_corr" not in quiet


def test_gate_passes_only_what_passed_the_check(tmp_path, monkeypatch, capsys):
    ids = ["good", "nanspec", "nospec", "accepted"]
    here = beside(tmp_path / "pipeline", {i: make_pin(i) for i in ids}, {"accept": {"accepted": "checked by ear"}})
    android_job(tmp_path / "art", "good", verify_report("good"))
    android_job(tmp_path / "art", "nanspec", verify_report("nanspec", spectrum_corr=math.nan))
    android_job(tmp_path / "art", "nospec", verify_report("nospec", drop=("spectrum_corr",)))
    android_job(tmp_path / "art", "accepted", verify_report("accepted", spectrum_corr=math.nan))
    assert "NaN" in (tmp_path / "art" / "cart-android-nanspec" / "verify" / "nanspec.json").read_text()
    monkeypatch.setattr(gate, "HERE", here)
    monkeypatch.setattr(sys, "argv", ["gate.py", "--artifacts", str(tmp_path / "art")])
    gate.main()
    out, err = capsys.readouterr()
    assert json.loads(out) == ["accepted", "good"]
    assert "nanspec: diverges (not published): spectrum r=nan" in err
    assert "nospec: diverges (not published): spectrum r=None" in err
