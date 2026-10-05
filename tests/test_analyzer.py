"""
Unit tests for the audio analysis pipeline.

All tests use synthetic audio — no real music files needed.
Demucs (vocal detection) is mocked out to avoid downloading the model.
"""

import numpy as np
import pytest

from soundwave.analyzer import (
    _dedupe_and_cap,
    _get_beat_times,
    _snap_cue,
    detect_breakdowns,
    detect_builds,
    detect_drops,
    detect_intro,
    detect_outros,
)
from soundwave.config import MAX_HOT_CUES
from soundwave.rekordbox.models import CuePoint


# ---------------------------------------------------------------------------
# Drop detection
# ---------------------------------------------------------------------------

class TestDropDetection:
    def test_detects_drop_in_energy_spike(self, signal_with_drop):
        y, sr = signal_with_drop
        drops = detect_drops(y, sr)
        assert len(drops) >= 1, "Expected at least one drop"
        drop_times = [d.time_sec for d in drops]
        assert any(28 <= t <= 36 for t in drop_times), (
            f"No drop detected near 32s, got: {drop_times}"
        )

    def test_drop_label_and_color(self, signal_with_drop):
        y, sr = signal_with_drop
        drops = detect_drops(y, sr)
        assert all(d.label == "DROP" for d in drops)
        assert all(d.color == 0xFF0000 for d in drops)

    def test_no_false_drop_on_constant_signal(self, constant_signal):
        y, sr = constant_signal
        drops = detect_drops(y, sr)
        assert len(drops) == 0, (
            f"Unexpected drops on constant signal: {[d.time_sec for d in drops]}"
        )

    def test_drop_times_are_non_negative(self, signal_with_drop):
        y, sr = signal_with_drop
        drops = detect_drops(y, sr)
        assert all(d.time_sec >= 0 for d in drops)

    def test_at_most_max_drops(self, signal_with_drop):
        from soundwave.config import MAX_DROPS
        y, sr = signal_with_drop
        drops = detect_drops(y, sr)
        assert len(drops) <= MAX_DROPS


# ---------------------------------------------------------------------------
# Build detection
# ---------------------------------------------------------------------------

class TestBuildDetection:
    def test_detects_build_before_drop(self, signal_with_drop):
        y, sr = signal_with_drop
        drops = detect_drops(y, sr)
        builds = detect_builds(y, sr, drops)
        if drops:
            assert len(builds) >= 1, "Expected a build before the detected drop"
            for build in builds:
                assert any(build.time_sec < d.time_sec for d in drops)

    def test_no_builds_without_drops(self, constant_signal):
        y, sr = constant_signal
        builds = detect_builds(y, sr, drops=[])
        assert builds == []

    def test_build_label(self, signal_with_drop):
        y, sr = signal_with_drop
        drops = detect_drops(y, sr)
        builds = detect_builds(y, sr, drops)
        assert all(b.label == "BUILD" for b in builds)


# ---------------------------------------------------------------------------
# Breakdown detection
# ---------------------------------------------------------------------------

class TestBreakdownDetection:
    def test_detects_breakdown_in_silence(self, signal_with_breakdown):
        y, sr = signal_with_breakdown
        breaks = detect_breakdowns(y, sr)
        assert len(breaks) >= 1, "Expected at least one breakdown"
        times = [b.time_sec for b in breaks]
        assert any(12 <= t <= 18 for t in times), (
            f"No breakdown near 15s, got: {times}"
        )

    def test_breakdown_label(self, signal_with_breakdown):
        y, sr = signal_with_breakdown
        breaks = detect_breakdowns(y, sr)
        assert all(b.label == "BREAK" for b in breaks)

    def test_no_breakdown_on_constant_signal(self, constant_signal):
        y, sr = constant_signal
        breaks = detect_breakdowns(y, sr)
        assert len(breaks) == 0


# ---------------------------------------------------------------------------
# Intro detection
# ---------------------------------------------------------------------------

class TestIntroDetection:
    def test_intro_placed_before_first_drop(self, signal_with_drop):
        y, sr = signal_with_drop
        drops = detect_drops(y, sr)
        _, beat_times = _get_beat_times(y, sr)
        intro = detect_intro(drops, beat_times)
        assert intro is not None
        if drops:
            assert intro.time_sec < drops[0].time_sec

    def test_intro_non_negative(self, signal_with_drop):
        y, sr = signal_with_drop
        drops = detect_drops(y, sr)
        _, beat_times = _get_beat_times(y, sr)
        intro = detect_intro(drops, beat_times)
        if intro:
            assert intro.time_sec >= 0

    def test_intro_label(self, signal_with_drop):
        y, sr = signal_with_drop
        drops = detect_drops(y, sr)
        _, beat_times = _get_beat_times(y, sr)
        intro = detect_intro(drops, beat_times)
        if intro:
            assert intro.label == "INTRO"

    def test_intro_snapped_to_beat(self, signal_with_drop):
        y, sr = signal_with_drop
        drops = detect_drops(y, sr)
        _, beat_times = _get_beat_times(y, sr)
        intro = detect_intro(drops, beat_times)
        if intro and len(beat_times):
            diffs = np.abs(beat_times - intro.time_sec)
            assert diffs.min() < 0.5, "Intro should land within 0.5s of a beat"


# ---------------------------------------------------------------------------
# Outro detection
# ---------------------------------------------------------------------------

class TestOutroDetection:
    def test_outro32_placed_before_outro16(self):
        cues = detect_outros(duration_sec=240.0, bpm=128.0)
        labels = {c.label: c.time_sec for c in cues}
        assert "OUTRO32" in labels
        assert "OUTRO16" in labels
        assert labels["OUTRO32"] < labels["OUTRO16"]

    def test_outro16_near_end(self):
        bpm = 128.0
        duration = 240.0
        beat_sec = 60.0 / bpm
        bar_sec = beat_sec * 4
        expected_16 = duration - bar_sec * 16

        cues = detect_outros(duration_sec=duration, bpm=bpm)
        labels = {c.label: c.time_sec for c in cues}
        assert "OUTRO16" in labels
        assert abs(labels["OUTRO16"] - expected_16) < 0.1

    def test_outro32_near_end(self):
        bpm = 128.0
        duration = 240.0
        beat_sec = 60.0 / bpm
        bar_sec = beat_sec * 4
        expected_32 = duration - bar_sec * 32

        cues = detect_outros(duration_sec=duration, bpm=bpm)
        labels = {c.label: c.time_sec for c in cues}
        assert "OUTRO32" in labels
        assert abs(labels["OUTRO32"] - expected_32) < 0.1

    def test_outros_non_negative(self):
        cues = detect_outros(duration_sec=240.0, bpm=128.0)
        assert all(c.time_sec > 0 for c in cues)

    def test_short_track_skips_outro32(self):
        # A 30s track at 128 BPM: 32 bars = ~60s, so OUTRO32 would be negative
        cues = detect_outros(duration_sec=30.0, bpm=128.0)
        labels = [c.label for c in cues]
        assert "OUTRO32" not in labels

    def test_outro_colors(self):
        cues = detect_outros(duration_sec=240.0, bpm=128.0)
        for c in cues:
            assert c.color != 0, "Outro cues should have a color set"


# ---------------------------------------------------------------------------
# Beat snapping
# ---------------------------------------------------------------------------

class TestBeatSnapping:
    def test_drop_snaps_to_beat_grid(self, signal_with_drop):
        y, sr = signal_with_drop
        _, beat_times = _get_beat_times(y, sr)
        cue = CuePoint("DROP", 32.3, 0xFF0000)
        snapped = _snap_cue(cue, beat_times)
        diffs = np.abs(beat_times - snapped.time_sec)
        assert diffs.min() < 0.5, f"DROP not snapped to a beat: {snapped.time_sec:.3f}s"

    def test_snap_preserves_label_and_color(self, signal_with_drop):
        y, sr = signal_with_drop
        _, beat_times = _get_beat_times(y, sr)
        cue = CuePoint("BREAK", 20.0, 0xFFFF00)
        snapped = _snap_cue(cue, beat_times)
        assert snapped.label == "BREAK"
        assert snapped.color == 0xFFFF00

    def test_snap_non_negative(self, signal_with_drop):
        y, sr = signal_with_drop
        _, beat_times = _get_beat_times(y, sr)
        cue = CuePoint("INTRO", 0.05, 0xFFFFFF)
        snapped = _snap_cue(cue, beat_times)
        assert snapped.time_sec >= 0.0


# ---------------------------------------------------------------------------
# Deduplication and cap
# ---------------------------------------------------------------------------

class TestDedupeAndCap:
    def test_caps_at_max_hot_cues(self):
        cues = [CuePoint("DROP", float(i * 10), 0xFF0000) for i in range(20)]
        result = _dedupe_and_cap(cues)
        assert len(result) <= MAX_HOT_CUES

    def test_deduplicates_same_label_within_1s(self):
        cues = [
            CuePoint("DROP", 10.0, 0xFF0000),
            CuePoint("DROP", 10.3, 0xFF0000),
            CuePoint("DROP", 30.0, 0xFF0000),
        ]
        result = _dedupe_and_cap(cues)
        times = [c.time_sec for c in result]
        assert 10.3 not in times

    def test_output_sorted_by_time(self):
        cues = [
            CuePoint("BUILD",   25.0, 0x00FF00),
            CuePoint("INTRO",    5.0, 0xFFFFFF),
            CuePoint("DROP",    32.0, 0xFF0000),
            CuePoint("OUTRO16", 90.0, 0xFF0080),
        ]
        result = _dedupe_and_cap(cues)
        times = [c.time_sec for c in result]
        assert times == sorted(times)

    def test_drops_prioritized_when_over_limit(self):
        drops = [CuePoint("DROP", float(i * 5), 0xFF0000) for i in range(6)]
        breaks = [CuePoint("BREAK", float(i * 5 + 2), 0xFFFF00) for i in range(6)]
        result = _dedupe_and_cap(drops + breaks)
        assert len(result) == MAX_HOT_CUES
        drop_count = sum(1 for c in result if c.label == "DROP")
        break_count = sum(1 for c in result if c.label == "BREAK")
        assert drop_count >= break_count

    def test_content_cues_prioritized_over_outros_when_over_limit(self):
        # OUTRO16/OUTRO32 are structural (derivable from BPM alone), not
        # analyzed content — when a track has enough real content cues to
        # fill all 8 slots, outros should lose out rather than crowd out
        # the actual analyzed moments (this was backwards before: outros
        # used to always win, which is why "important" cues felt missing).
        outros = [CuePoint("OUTRO16", 180.0, 0xFF0080), CuePoint("OUTRO32", 150.0, 0xFF6400)]
        breaks = [CuePoint("BREAK", float(i * 10), 0xFFFF00) for i in range(8)]
        result = _dedupe_and_cap(outros + breaks)
        labels = [c.label for c in result]
        assert len(result) == MAX_HOT_CUES
        assert "OUTRO16" not in labels
        assert "OUTRO32" not in labels
        assert labels.count("BREAK") == MAX_HOT_CUES

    def test_outros_fill_leftover_slots(self):
        # With few competing content cues, outros should still show up.
        outros = [CuePoint("OUTRO16", 180.0, 0xFF0080), CuePoint("OUTRO32", 150.0, 0xFF6400)]
        breaks = [CuePoint("BREAK", 10.0, 0xFFFF00)]
        result = _dedupe_and_cap(outros + breaks)
        labels = [c.label for c in result]
        assert "OUTRO16" in labels
        assert "OUTRO32" in labels
        assert "BREAK" in labels

    def test_round_robin_preserves_type_diversity(self):
        # A track with many drops shouldn't crowd out its only build/vocal/
        # section/break — each present type should get at least one slot
        # before any type claims a second.
        drops = [CuePoint("DROP", float(i * 5), 0xFF0000) for i in range(6)]
        builds = [CuePoint("BUILD", 1.0, 0x00FF00)]
        vocals = [CuePoint("VOCAL", 2.0, 0x0000FF)]
        sections = [CuePoint("SECTION", 3.0, 0x9932CC)]
        breaks = [CuePoint("BREAK", 4.0, 0xFFFF00)]
        result = _dedupe_and_cap(drops + builds + vocals + sections + breaks)
        labels = {c.label for c in result}
        assert labels == {"DROP", "BUILD", "VOCAL", "SECTION", "BREAK"}
