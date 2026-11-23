"""Pinned expressive firmware identity and publication proof, shared by the pipeline stages."""
import hashlib
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
CAPS = {"pitch_bend", "note_pressure"}
SHA = re.compile(r"[0-9a-f]{40}")
NAME = re.compile(r"[a-z0-9_]+")
CHECKS = frozenset({
    "audible_reference", "polyphonic_reference", "square_law_pressure", "pressure_zero",
    "pressure_restored", "per_note_isolation", "retrigger_resets_pressure",
    "channel_isolation", "pitch_bend", "key_bend", "panic_recovery",
})


def required_checks(fid, value):
    """Every check a configured firmware's proof must pass: the shared audio checks, the Felucca lifecycle where it
    applies, and one neutral_<name> per test of its own suite (`neutral` in the manifest)."""
    return (CHECKS | ({"voice_lifecycle"} if fid in {"skiff", "caravel"} else set())
            | {f"neutral_{t['name']}" for t in value["neutral"]})


def safe_path(p):
    return isinstance(p, str) and p != "" and not p.startswith("/") and ".." not in Path(p).parts


class ExpressionError(ValueError):
    pass


def entry(fid, directory=HERE):
    """The configured manifest entry, or None for ordinary firmware."""
    path = Path(directory) / "expression.json"
    if not path.exists():
        return None
    try:
        manifest = json.loads(path.read_text())
    except (OSError, ValueError) as ex:
        raise ExpressionError(f"cannot read expression manifest: {ex}") from ex
    if not isinstance(manifest, dict):
        raise ExpressionError("expression manifest must map firmware ids to entries")
    if fid not in manifest:
        return None
    value = manifest[fid]
    if not isinstance(value, dict) or not SHA.fullmatch(value.get("commit") or ""):
        raise ExpressionError(f"{fid}: expression manifest needs a full source commit")
    caps = value.get("caps")
    if not isinstance(caps, dict) or not caps or set(caps) - CAPS or any(type(v) is not bool for v in caps.values()):
        raise ExpressionError(f"{fid}: invalid expression capabilities")
    # the firmware's own tests, run on the patched source and with expression.patch reversed: byte-identical outputs
    # prove the patch changes nothing else. Every expressive firmware needs at least one.
    neutral = value.get("neutral")
    if (not isinstance(neutral, list) or not neutral
            or any(not isinstance(t, dict) or not NAME.fullmatch(str(t.get("name", ""))) or not isinstance(t.get("run"), str)
                   or not t["run"].strip() or not isinstance(t.get("compare"), list) or not t["compare"]
                   or not all(safe_path(p) for p in t["compare"]) for t in neutral)
            or len({t["name"] for t in neutral}) != len(neutral)):
        raise ExpressionError(f"{fid}: expression manifest needs neutral tests (name, run, compare)")
    return value


def identity(fid, commit=None, directory=HERE):
    """{revision, caps}, or None if unconfigured; reject a pin that the patch does not support.

    Revision is SHA256 of canonical JSON for this entry, then NUL + filename + NUL + bytes
    for each of this firmware's patches in filename order. Other firmware changes do not affect it.
    """
    value = entry(fid, directory)
    if value is None:
        return None
    if commit is not None and commit != value["commit"]:
        raise ExpressionError(f"{fid}: expression source commit {value['commit']} does not match pin {commit}")
    patches = sorted((Path(directory) / "patches" / fid).glob("*.patch"))
    if not (Path(directory) / "patches" / fid / "expression.patch").is_file():
        raise ExpressionError(f"{fid}: expression patches are missing (patches/{fid}/expression.patch)")
    digest = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())
    for patch in patches:
        digest.update(b"\0" + patch.name.encode() + b"\0" + patch.read_bytes())
    return {"revision": digest.hexdigest(), "caps": dict(value["caps"])}


def matches(actual, expected):
    """Identity fields must agree exactly, including boolean capability values."""
    return (isinstance(actual, dict) and actual.get("revision") == expected["revision"]
            and isinstance(actual.get("caps"), dict) and actual["caps"] == expected["caps"]
            and all(type(v) is bool for v in actual["caps"].values()))


def proof_errors(fid, commit, results, verification, targets=(), directory=HERE):
    """Return (configured identity or None, blocking errors); ordinary firmware needs no proof."""
    try:
        expected = identity(fid, commit, directory)
    except ExpressionError as ex:
        return None, [str(ex)]
    if expected is None:
        return None, []
    errors = []
    for target in dict.fromkeys(("host", *targets)):
        result = results.get(target)
        if not isinstance(result, dict) or result.get("ok") is not True or not matches(result.get("expression"), expected):
            errors.append(f"{target}: missing or stale expression build identity")
    expression = verification.get("expression") if isinstance(verification, dict) else None
    if not isinstance(verification, dict) or verification.get("id") != fid:
        errors.append("missing expression verification for this firmware")
    if not isinstance(expression, dict) or expression.get("ok") is not True or not matches(expression, expected):
        errors.append("missing, failed or stale expression verification")
    else:
        checks = expression.get("checks")
        if not isinstance(checks, dict) or not required_checks(fid, entry(fid, directory)).issubset(checks) or any(
                not isinstance(result, dict) or result.get("ok") is not True
                for result in checks.values()):
            errors.append("expression checks are missing or failed")
    return expected, errors
