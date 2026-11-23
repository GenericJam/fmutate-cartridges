"""The publication gate must measure real stereo audio, including inverted channels."""
import numpy as np
import pytest

from expression_verify import db_ratio, note_energy, pitch, rms


def tone(hz, amplitude=1000, inverted=False):
    sample = amplitude * np.sin(2 * np.pi * hz * np.arange(22050) / 44100)
    return np.column_stack((sample, -sample if inverted else sample))


def test_pitch_distinguishes_center_and_both_bend_endpoints():
    center = 261.625565
    measured = [pitch(tone(center * 2 ** (s / 12)), 60) for s in (0, 2, -2)]
    cents = [1200 * np.log2(hz / measured[0]) for hz in measured]
    assert cents == pytest.approx([0, 200, -200], abs=1)


def test_opposite_stereo_phase_is_not_mistaken_for_silence():
    audio = tone(261.625565, inverted=True)
    assert rms(audio) == pytest.approx(1000 / np.sqrt(2), rel=.001)
    assert note_energy(audio, 60) == pytest.approx(note_energy(tone(261.625565), 60), rel=.001)


def test_per_note_measurement_separates_one_muted_note_from_whole_chord_gain():
    first = tone(261.625565)
    other = tone(369.994423)
    chord = first + other
    target_drop = db_ratio(note_energy(other, 60), note_energy(chord, 60))
    other_change = db_ratio(note_energy(other, 66), note_energy(chord, 66))
    assert target_drop < -60
    assert abs(other_change) < .01
    # A global gain fails the unchanged-other-note requirement.
    assert db_ratio(note_energy(chord * .1, 66), note_energy(chord, 66)) == pytest.approx(-20)
