"""BREAK and BUILD detection from kick structure, on synthetic signals."""

import numpy as np

from soundwave.analyzer import (
    _drop_builds_on_breaks, analyze_kick_structure, breaks_from_structure,
    builds_from_structure, drops_from_structure,
)
from soundwave.rekordbox.models import CuePoint
from tests.test_drum_drops import BEAT, SR, pad, track

BAR = 4 * BEAT  # 2 s at 120 BPM


def structure(drum_sections, mix_levels, duration, extra=None):
    y, drums = track(drum_sections, mix_levels, duration)
    if extra is not None:
        y = y + pad(extra, len(y))
    return y, drums, analyze_kick_structure(y, drums, SR)


def riser(start, end, lo=0.005, hi=0.3, duration=None, seed=1):
    """Broadband noise ramping up in level from `start` to `end`."""
    rng = np.random.default_rng(seed)
    n = int((duration or end) * SR)
    y = np.zeros(n, dtype=np.float32)
    a, b = int(start * SR), int(end * SR)
    y[a:b] = (rng.standard_normal(b - a) * np.linspace(lo, hi, b - a)).astype(np.float32)
    return y


class TestBreaks:
    def test_break_starts_at_first_missing_kick(self):
        # kicks to 40s (last thump at 39.5), nothing 40-60, back at 60
        _, _, ks = structure([(0, 40), (60, 90)], [(0, 90, 0.2)], 90)
        (b,) = breaks_from_structure(ks, BEAT)
        assert b.label == "BREAK" and abs(b.time_sec - 40.0) < 0.2

    def test_intro_without_kick_is_not_a_break(self):
        _, _, ks = structure([(16, 60)], [(0, 60, 0.2)], 60)
        assert breaks_from_structure(ks, BEAT) == []

    def test_outro_without_kick_is_not_a_break(self):
        _, _, ks = structure([(0, 40)], [(0, 60, 0.2)], 60)
        assert breaks_from_structure(ks, BEAT) == []

    def test_longest_break_first(self):
        _, _, ks = structure([(0, 20), (28, 50), (70, 100)], [(0, 100, 0.2)], 100)
        times = [round(b.time_sec) for b in breaks_from_structure(ks, BEAT)]
        assert times == [50, 20]  # the 20s gap before the 70s return outranks the 8s gap


class TestBuilds:
    def build_track(self, riser_start=32.0, drop=48.0, with_riser=True):
        extra = riser(riser_start, drop, duration=drop + 12) if with_riser else None
        y, drums, ks = structure([(0, 24), (drop, drop + 12)],
                                 [(0, 24, 0.1), (24, drop, 0.1), (drop, drop + 12, 0.4)], drop + 12, extra)
        return y, ks, drops_from_structure(ks)

    def test_build_found_and_lands_on_the_phrase_grid_before_the_drop(self):
        y, ks, drops = self.build_track()
        assert len(drops) == 1
        (b,) = builds_from_structure(y, SR, ks, drops, BAR)
        assert b.label == "BUILD" and 0 < b.time_sec < drops[0].time_sec
        phrases_before = (drops[0].time_sec - b.time_sec) / (4 * BAR)
        assert abs(phrases_before - round(phrases_before)) < 0.05   # whole 4-bar phrases
        assert 28 <= b.time_sec <= 40                               # near where the riser starts

    def test_no_riser_no_build(self):
        y, ks, drops = self.build_track(with_riser=False)
        assert drops and builds_from_structure(y, SR, ks, drops, BAR) == []

    def test_loud_passage_far_before_the_drop_is_not_a_build(self):
        # a noise swell that peaks and ends ~20s before the drop
        extra = riser(2.0, 14.0, duration=60)
        y, drums, ks = structure([(24, 36), (48, 60)], [(0, 60, 0.1), (48, 60, 0.4)], 60, extra)
        drops = [CuePoint("DROP", 48.0, 0)]
        assert builds_from_structure(y, SR, ks, drops, BAR) == []


class TestBuildBreakDedupe:
    def test_build_at_the_break_is_removed(self):
        builds = [CuePoint("BUILD", 40.5, 0), CuePoint("BUILD", 80.0, 0)]
        breaks = [CuePoint("BREAK", 40.0, 0)]
        kept = _drop_builds_on_breaks(builds, breaks, BAR)
        assert [b.time_sec for b in kept] == [80.0]
