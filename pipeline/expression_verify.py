#!/usr/bin/env python3
"""Render native cartridges offline; prove MIDI expression, not merely message delivery.

No audio device is opened. verify.py's chord/PLAY comparison with the emulator stays a separate gate; the neutral
checks here run the firmware's own tests on the patched source and on it with expression.patch reversed, and
require byte-identical outputs: the patch changes nothing but expression.
The same square-law pressure contract as FMutate's OberVave patch is required.
"""
import argparse
import json
import math
import hashlib
import shlex
import shutil
import os
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from common import CAT, HERE, PINS, SRC, target_cc
from expression_config import entry, identity

SHIM = HERE / 'shims/include'
SHIM_FLAGS = f"-I{shlex.quote(str(SHIM))} -include {shlex.quote(str(SHIM / 'fm1_native_shims.h'))}"

FS = 44100


def db_ratio(value, reference):
    return float(20 * np.log10(max(value, 1e-12) / max(reference, 1e-12)))


def rms(audio):
    return float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))


def note_hz(note):
    return 440 * 2 ** ((note - 69) / 12)


def spectrum(audio):
    x = audio.astype(np.float64)
    x -= x.mean(axis=0)
    power = np.sum(np.abs(np.fft.rfft(x * np.hanning(len(x))[:, None], axis=0)) ** 2, axis=1)
    return np.fft.rfftfreq(len(x), 1 / FS), power


def note_energy(audio, note):
    """Fundamental band only: the pilot's tritone avoids shared low harmonics."""
    frequencies, power = spectrum(audio)
    hz = note_hz(note)
    return float(np.sqrt(power[(frequencies > hz * .97) & (frequencies < hz * 1.03)].sum()))


def pitch(audio, note):
    """Strongest fundamental near the played note, with quadratic FFT interpolation."""
    frequencies, power = spectrum(audio)
    hz = note_hz(note)
    indices = np.flatnonzero((frequencies > hz * .8) & (frequencies < hz * 1.25))
    i = int(indices[np.argmax(power[indices])])
    logs = np.log(power[i - 1:i + 2] + 1e-30)
    denominator = logs[0] - 2 * logs[1] + logs[2]
    offset = .5 * (logs[0] - logs[2]) / denominator if abs(denominator) > 1e-12 else 0
    return float((i + np.clip(offset, -.5, .5)) * FS / len(audio))


def render(Engine, cart, stages):
    """Each stage is (milliseconds, MIDI messages); collect its last 500 ms."""
    host = Path(os.environ.get('FM1_ENGINE_DIR') or Path(__file__).resolve().parent.parent / 'engine')
    engine = Engine(host / 'libfm1engine.so', cart)
    result = []
    try:
        engine.drain(FS * 4)
        for ms, messages in stages:
            for message in messages:
                if message[0] == 'key':                 # ('key', FM-1 key, down): the panel, op 0x01
                    engine.send(0x01, message[1], message[2])
                else:
                    engine.send(0x04, *message)
            samples = engine.drain(FS * ms // 1000)
            result.append(samples[-FS // 2:])
        return result
    finally:
        engine.close()
        for name in ('ring', 'fb'):
            mapping = getattr(engine, name, None)
            if mapping is not None:
                mapping.close()
        for stream in (engine.p.stdin, engine.p.stdout, engine.p.stderr):
            if stream is not None:
                stream.close()
        shutil.rmtree(engine.dir)


def voice_lifecycle(fid):
    """Render source-key lifecycle edges with the firmware's real host harness."""
    source = SRC / fid
    executable = CAT / 'obj/host' / fid / 'expression-lifecycle'
    executable.parent.mkdir(parents=True, exist_ok=True)
    cc, flags, _ = target_cc('host')
    command = [*cc, *flags, '-O1', '-w', '-Ibuild/gen', '-Ifirmware/src', '-Itests',
               str(HERE / 'expression_felucca.c'), '-o', str(executable), '-lm']
    compiled = subprocess.run(command, cwd=source, capture_output=True, text=True, timeout=120)
    if compiled.returncode:
        return {'ok': False, 'error': compiled.stderr[-4000:]}
    result = subprocess.run([str(executable)], cwd=source, capture_output=True, text=True, timeout=60)
    return {'ok': result.returncode == 0, 'log': result.stdout, 'error': result.stderr}


def exercise(fid, Engine, cart):
    spec = entry(fid)
    a, b = spec['notes']
    note_on = lambda n: (0x90, n, 127)
    pressure = lambda n, level: (0xA0, n, level)
    bend = lambda value: (0xE0, value & 127, value >> 7)
    checks = {}
    # SAW LEAD boots in LEGATO on Skiff/Caravel. Select SINE KEY (poly)
    # and disable sends through ordinary MIDI; isolation needs two real notes.
    def draw(stages):
        setup = spec.get('setup', [])
        return render(Engine, cart, setup + stages)[len(setup):]

    # Comparisons use identical elapsed note time: envelope decay cannot impersonate pressure.
    full = draw([(1800, [note_on(a)]), (1800, [])])[-1]
    half = draw([(1800, [note_on(a)]), (1800, [pressure(a, 64)])])[-1]
    zero = draw([(1800, [note_on(a)]), (2400, [pressure(a, 0)])])[-1]
    reference = rms(full)
    checks['audible_reference'] = {'ok': reference >= 32, 'rms': reference}
    attenuation = db_ratio(rms(half), reference)
    checks['square_law_pressure'] = {'ok': -16 <= attenuation <= -8, 'db': attenuation}
    attenuation = db_ratio(rms(zero), reference)
    checks['pressure_zero'] = {'ok': attenuation <= -24, 'db': attenuation}

    restored = draw([(1800, [note_on(a)]), (1800, [pressure(a, 0)]),
                     (1800, [pressure(a, 127)])])[-1]
    restored_reference = draw([(5400, [note_on(a)])])[-1]
    recovery = db_ratio(rms(restored), rms(restored_reference))
    checks['pressure_restored'] = {'ok': abs(recovery) <= 2, 'db': recovery}

    chord = [note_on(a), note_on(b)]
    control = draw([(1800, chord), (2400, [])])[-1]
    isolated = draw([(1800, chord), (2400, [pressure(a, 0)])])[-1]
    _, power = spectrum(control)
    energy = float(np.sqrt(power.sum()))
    checks['polyphonic_reference'] = {
        'ok': energy > 0 and min(note_energy(control, a), note_energy(control, b)) >= .05 * energy}
    target = db_ratio(note_energy(isolated, a), note_energy(control, a))
    other = db_ratio(note_energy(isolated, b), note_energy(control, b))
    checks['per_note_isolation'] = {'ok': target <= -18 and abs(other) <= 3,
                                    'target_db': target, 'other_db': other}

    # A released, muted key must not poison either a retrigger or newly allocated voice.
    retrigger = draw([(1000, [note_on(a), pressure(a, 0)]),
                      (1200, [(0x80, a, 0)]), (1800, [note_on(a)])])[-1]
    clean = draw([(2200, []), (1800, [note_on(a)])])[-1]
    recovery = db_ratio(rms(retrigger), rms(clean))
    checks['retrigger_resets_pressure'] = {'ok': abs(recovery) <= 2, 'db': recovery}

    # MIDI channel 2 must not silence the channel-1 voice.
    wrong_channel = draw([(1800, [note_on(a)]), (1800, [(0xA1, a, 0)])])[-1]
    separation = db_ratio(rms(wrong_channel), reference)
    checks['channel_isolation'] = {'ok': abs(separation) <= 2, 'db': separation}

    # Use whole rendered audio, not firmware-reported pitch or a forwarded MIDI echo.
    bent = draw([(1800, [note_on(a)]), (1200, [bend(16383)]),
                 (1200, [bend(0)]), (1200, [bend(8192)])])
    frequencies = [pitch(audio, a) for audio in bent]
    cents = [float(1200 * math.log2(hz / frequencies[0])) for hz in frequencies]
    expected = spec['bend_semitones'] * 100
    checks['pitch_bend'] = {'ok': abs(cents[1] - expected) <= 20 and abs(cents[2] + expected) <= 20
                          and abs(cents[3]) <= 10, 'cents': cents, 'frequencies': frequencies}

    # Panic must release muted and sounding keys; a later key starts at normal level and centered pitch.
    panic = draw([(1000, [note_on(a), pressure(a, 0), bend(16383)]),
                  (1600, [(0xB0, 120, 0), (0xB0, 121, 0), bend(8192)]),
                  (1800, [note_on(a)])])[-1]
    recovery = db_ratio(rms(panic), rms(clean))
    reset_cents = 1200 * math.log2(pitch(panic, a) / pitch(clean, a))
    checks['panic_recovery'] = {'ok': abs(recovery) <= 2 and abs(reset_cents) <= 10,
                                'db': recovery, 'cents': reset_cents}

    # All Notes Off (CC123, a DAW's loop point or transport stop) lets notes go but keeps a held bend: only CC121
    # resets controllers. The next note sounds bent.
    held = draw([(1000, [note_on(a), bend(16383)]), (600, [(0xB0, 123, 0)]), (1800, [note_on(a)])])[-1]
    held_cents = float(1200 * math.log2(pitch(held, a) / pitch(clean, a)))
    checks['notes_off_keeps_bend'] = {'ok': abs(held_cents - expected) <= 20, 'cents': held_cents}

    # FMutate bends strips that play the firmware's own keys too: MIDI bend on channel 1 must reach them.
    key = 7                                     # FM-1 key 7: C4 at octave 0
    keyed = draw([(1800, [('key', key, 1)]), (1200, [bend(16383)]), (1200, [bend(8192)])])
    base = pitch(keyed[0], a)
    key_cents = [float(1200 * math.log2(pitch(audio, a) / base)) for audio in keyed]
    checks['key_bend'] = {'ok': abs(key_cents[1] - expected) <= 20 and abs(key_cents[2]) <= 10,
                          'cents': key_cents}
    if fid in ('skiff', 'caravel'):
        checks['voice_lifecycle'] = voice_lifecycle(fid)
    return checks


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def neutral(fid, spec, source=None, patch=None):
    """{neutral_<name>: result} for each of the firmware's own tests (`neutral` in the manifest): `run` (a shell
    command; {shim} becomes the native shims' compiler flags) on a copy of the patched source and on a copy with only
    expression.patch reversed. Passes when both exit 0 and every `compare` file exists and is byte-identical."""
    source = Path(source or SRC / fid)
    patch = Path(patch or HERE / 'patches' / fid / 'expression.patch')
    with tempfile.TemporaryDirectory(prefix=f'neutral-{fid}-') as tmp:
        trees = {'patched': Path(tmp, 'patched'), 'pristine': Path(tmp, 'pristine')}
        for tree in trees.values():
            shutil.copytree(source, tree, symlinks=True, ignore=shutil.ignore_patterns('.git'))
        undo = subprocess.run(['patch', '-p1', '-R', '-s', '-f', '-i', str(patch)], cwd=trees['pristine'],
                              capture_output=True, text=True)
        if undo.returncode:
            why = 'expression.patch does not reverse: ' + (undo.stdout + undo.stderr)[-800:]
            return {f"neutral_{t['name']}": {'ok': False, 'error': why} for t in spec['neutral']}
        checks = {}
        for t in spec['neutral']:
            runs = {}
            for side, tree in trees.items():
                for p in t['compare']:                      # (a test that writes nothing must not pass on an old file)
                    (tree / p).unlink(missing_ok=True)
                try:
                    r = subprocess.run(t['run'].replace('{shim}', SHIM_FLAGS), shell=True, cwd=tree,
                                       capture_output=True, text=True, timeout=600)
                    rc, log = r.returncode, (r.stdout + r.stderr)[-1500:]
                except subprocess.TimeoutExpired:
                    rc, log = 'timeout', ''
                runs[side] = {'rc': rc, 'files': {p: digest(tree / p) for p in t['compare']}}
                if rc != 0:
                    runs[side]['log'] = log
            files = runs['patched']['files']
            checks[f"neutral_{t['name']}"] = {
                'ok': all(r['rc'] == 0 for r in runs.values()) and all(files.values())
                      and files == runs['pristine']['files'], **runs}
        return checks


def check(fid, Engine):
    report = {'ok': False, 'checks': {}}
    try:
        report.update(identity(fid, PINS[fid]['commit']))
        report['checks'] = exercise(fid, Engine, CAT / 'out/host' / f'libcart_{fid}.so')
        report['checks'].update(neutral(fid, entry(fid)))
        report['ok'] = all(result['ok'] for result in report['checks'].values())
    except Exception as error:
        report['error'] = str(error)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('id')
    args = parser.parse_args()
    from verify import Engine
    report = check(args.id, Engine)
    destination = CAT / 'verify' / f'{args.id}.expression.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=1, allow_nan=False) + '\n')
    print(json.dumps(report, indent=1, allow_nan=False))
    raise SystemExit(0 if report['ok'] else 1)


if __name__ == '__main__':
    main()
