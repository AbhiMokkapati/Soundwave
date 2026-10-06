"""Drum-stem drop detection on synthetic signals (no Demucs needed): the
detector takes the mix and the drum stem as plain arrays."""

import numpy as np

from soundwave.analyzer import detect_drum_drops

SR = 22050
BPM = 120
BEAT = 60.0 / BPM


def kicks(start, end, level=1.0, sr=SR):
    """Decaying 60 Hz thumps on every beat in [start, end)."""
    y = np.zeros(int(end * sr), dtype=np.float32)
    n = int(0.12 * sr)
    t = np.arange(n) / sr
    thump = (np.sin(2 * np.pi * 60 * t) * np.exp(-t * 30) * level).astype(np.float32)
    for b in np.arange(start, end, BEAT):
        i = int(b * sr)
        y[i:i + n] += thump[: len(y) - i][: n]
    return y


def hats(start, end, level=0.1, sr=SR, seed=0):
    rng = np.random.default_rng(seed)
    y = np.zeros(int(end * sr), dtype=np.float32)
    y[int(start * sr):] = (rng.standard_normal(len(y) - int(start * sr)) * level).astype(np.float32)
    return y


def pad(y, n):
    return np.pad(y, (0, max(0, n - len(y))))[:n]


def track(drum_sections, mix_levels, duration):
    """drum_sections: [(start, end)] where kicks play. mix_levels: [(start, end, amp)]
    of a sustained tone making up the rest of the mix."""
    n = int(duration * SR)
    drums = np.zeros(n, dtype=np.float32)
    for a, b in drum_sections:
        drums += pad(kicks(a, b), n)
    t = np.arange(n) / SR
    pad_tone = np.zeros(n, dtype=np.float32)
    for a, b, amp in mix_levels:
        ia, ib = int(a * SR), int(b * SR)
        pad_tone[ia:ib] = (np.sin(2 * np.pi * 440 * t[ia:ib]) * amp).astype(np.float32)
    return drums + pad_tone, drums


class TestDrumDrops:
    def test_drop_is_where_the_kick_returns_after_a_gap(self):
        # kick 0-20, gap 20-32 (quiet pad), kick returns at 32 with a louder mix
        y, drums = track([(0, 20), (32, 60)], [(0, 20, 0.1), (20, 32, 0.1), (32, 60, 0.4)], 60)
        drops = detect_drum_drops(y, drums, SR)
        assert len(drops) == 1
        assert abs(drops[0].time_sec - 32.0) < 0.15

    def test_kickless_pickup_before_the_drop_does_not_pull_the_cue_early(self):
        y, drums = track([(0, 20), (32, 60)], [(0, 20, 0.1), (20, 32, 0.1), (32, 60, 0.4)], 60)
        # a hat/snare-ish noise fill 1.5s before the kick returns (no low end)
        fill_start = int(30.5 * SR)
        fill = hats(0, 32, level=0.3)
        fill[: fill_start] = 0
        fill = pad(fill, len(drums))
        drums = drums + fill
        y = y + fill
        drops = detect_drum_drops(y, drums, SR)
        assert abs(drops[0].time_sec - 32.0) < 0.15

    def test_kick_returning_without_a_louder_mix_is_not_a_drop(self):
        y, drums = track([(0, 20), (32, 60)], [(0, 60, 0.3)], 60)
        assert detect_drum_drops(y, drums, SR) == []

    def test_continuous_kick_has_no_drop(self):
        y, drums = track([(0, 60)], [(0, 60, 0.3)], 60)
        assert detect_drum_drops(y, drums, SR) == []

    def test_short_gap_is_not_a_break(self):
        # a 2s kick dropout (a fill) is shorter than DRUM_GAP_MIN_SEC
        y, drums = track([(0, 28), (30, 60)], [(0, 30, 0.1), (30, 60, 0.4)], 60)
        assert detect_drum_drops(y, drums, SR) == []

    def test_trailing_gap_is_an_outro_not_a_drop(self):
        y, drums = track([(0, 40)], [(0, 40, 0.3), (40, 60, 0.05)], 60)
        assert detect_drum_drops(y, drums, SR) == []

    def test_leading_kickless_intro_ending_in_a_louder_groove_is_a_drop(self):
        y, drums = track([(16, 60)], [(0, 16, 0.1), (16, 60, 0.4)], 60)
        drops = detect_drum_drops(y, drums, SR)
        assert len(drops) == 1 and abs(drops[0].time_sec - 16.0) < 0.15

    def test_strongest_contrast_ranked_first(self):
        y, drums = track(
            [(0, 16), (24, 44), (52, 80)],
            [(0, 16, 0.1), (16, 24, 0.1), (24, 44, 0.25), (44, 52, 0.1), (52, 80, 0.6)], 80,
        )
        drops = detect_drum_drops(y, drums, SR)
        assert [round(d.time_sec) for d in drops] == [52, 24]

    def test_silent_drum_stem_returns_nothing(self):
        y, _ = track([], [(0, 30, 0.3)], 30)
        assert detect_drum_drops(y, np.zeros_like(y), SR) == []
