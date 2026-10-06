"""Rekordbox-grid snapping and the cue-placement metrics (synthetic grids; no
Rekordbox install or audio needed)."""

import numpy as np

from soundwave.analyzer import BeatGrid, _cache_key, _snap_cue, detect_intro, grid_fingerprint
from soundwave.evaluate import (
    chorus_starts, cue_reference_metrics, drop_chorus_alignment, grid_agreement,
    summarize_reference, truth_metrics,
)
from soundwave.rekordbox.anlz_grid import Phrase, RekordboxGrid
from soundwave.rekordbox.models import CuePoint

BEAT = 0.5  # 120 BPM


def make_grid(n_beats=64, first_number=1, phrases=()):
    """120 BPM grid starting at 1.0s. first_number != 1 starts the grid mid-bar."""
    times = 1.0 + BEAT * np.arange(n_beats)
    numbers = (np.arange(n_beats) + first_number - 1) % 4 + 1
    return RekordboxGrid(times, numbers, np.full(n_beats, 120.0), list(phrases))


class TestDownbeats:
    def test_downbeats_are_beat_number_one(self):
        g = make_grid(first_number=3)  # grid starts on beat 3 of a bar
        assert np.allclose(g.downbeat_times[:2], [2.0, 4.0])

    def test_bpm_is_median(self):
        assert make_grid().bpm == 120.0


class TestDownbeatSnapping:
    def test_drop_snaps_to_real_bar_start_not_every_fourth_beat(self):
        # Grid starts mid-bar, so "every 4th beat from the first" is the
        # wrong phase — the old behaviour. Bar starts are at 2.0, 4.0, ...
        g = make_grid(first_number=3)
        snapped = _snap_cue(CuePoint("DROP", 8.6, 0), g.beat_times, g.downbeat_times)
        assert snapped.time_sec == 8.0
        # legacy every-4th-beat from beat 0 would have picked 9.0, which is mid-bar
        assert _snap_cue(CuePoint("DROP", 8.6, 0), g.beat_times).time_sec == 9.0

    def test_legacy_snap_without_downbeats_unchanged(self):
        g = make_grid()
        snapped = _snap_cue(CuePoint("DROP", 9.1, 0), g.beat_times)
        assert snapped.time_sec == 9.0  # every 4th beat from the first beat

    def test_vocal_snaps_to_beat_not_bar(self):
        g = make_grid()
        snapped = _snap_cue(CuePoint("VOCAL", 8.6, 0), g.beat_times, g.downbeat_times)
        assert snapped.time_sec == 8.5  # pickup beat kept, not dragged to the bar at 9.0

    def test_structural_cues_land_on_downbeats(self):
        g = make_grid(first_number=2)
        for label in ("INTRO", "BUILD", "BREAK", "SECTION", "OUTRO16", "OUTRO32"):
            t = _snap_cue(CuePoint(label, 13.37, 0), g.beat_times, g.downbeat_times).time_sec
            assert t in g.downbeat_times


class TestIntro:
    def test_intro_is_first_real_bar_start_when_grid_begins_mid_bar(self):
        g = make_grid(first_number=3)  # grid starts on beat 3: first bar line is 2.0, not 1.0
        intro = detect_intro(g.beat_times, g.downbeat_times)
        assert intro.time_sec == 2.0 and intro.time_sec in g.downbeat_times

    def test_intro_legacy_guess_without_downbeats_is_first_beat(self):
        g = make_grid(first_number=3)
        assert detect_intro(g.beat_times).time_sec == 1.0

    def test_intro_follows_audio_start_not_the_drop(self):
        g = make_grid(first_number=3)
        assert detect_intro(g.beat_times, g.downbeat_times, audio_start_sec=9.0).time_sec == 10.0


class TestCacheKey:
    def test_grid_identity_changes_key(self, tmp_path):
        f = tmp_path / "a.wav"
        f.write_bytes(b"0")
        assert _cache_key(str(f), True, "rb:1") != _cache_key(str(f), True, "rb:2")
        assert _cache_key(str(f), True, "none") != _cache_key(str(f), True, "rb:1")

    def test_no_index_fingerprint(self):
        assert grid_fingerprint("x.wav", None) == "none"


class TestReferenceMetrics:
    def test_on_grid_cue(self):
        g = make_grid()
        (r,) = cue_reference_metrics([CuePoint("DROP", 9.0, 0)], g)
        assert r.beat_off_ms == 0 and r.downbeat_off_ms == 0

    def test_off_by_one_beat_is_on_beat_but_not_on_bar(self):
        g = make_grid()
        (r,) = cue_reference_metrics([CuePoint("DROP", 9.5, 0)], g)
        assert r.beat_off_ms == 0 and r.downbeat_off_ms == 500

    def test_phrase_distance_in_beats(self):
        g = make_grid(phrases=[Phrase(9.0, 1, 1)])
        (r,) = cue_reference_metrics([CuePoint("DROP", 11.0, 0)], g)
        assert r.phrase_off_beats == 4.0

    def test_no_phrases_is_none_not_zero(self):
        (r,) = cue_reference_metrics([CuePoint("DROP", 9.0, 0)], make_grid())
        assert r.phrase_off_beats is None
        assert summarize_reference([r])["ALL"]["on_phrase_pct"] is None

    def test_summary_rolls_up_per_label(self):
        g = make_grid()
        refs = cue_reference_metrics([CuePoint("DROP", 9.0, 0), CuePoint("DROP", 9.5, 0)], g)
        s = summarize_reference(refs)
        assert s["DROP"]["n"] == 2 and s["DROP"]["on_downbeat_pct"] == 50.0


class TestGridAgreement:
    def test_detects_late_beats_and_wrong_bar_phase(self):
        g = make_grid(first_number=3)           # tracker assumes beat 0 is a bar start; it isn't
        tracker = g.beat_times + 0.040          # and runs 40 ms late
        a = grid_agreement(tracker, g)
        assert round(a.median_offset_ms) == 40
        assert a.bar_phase_ok_pct == 0.0

    def test_matching_phase(self):
        g = make_grid(first_number=1)
        a = grid_agreement(g.beat_times, g)
        assert a.bar_phase_ok_pct == 100.0 and a.median_offset_ms == 0


class TestTruthMetrics:
    def test_exact_and_near_hits(self):
        pred = [CuePoint("DROP", 10.0, 0), CuePoint("BREAK", 20.2, 0)]
        truth = [{"label": "DROP", "time_sec": 10.0}, {"label": "BREAK", "time_sec": 20.0}]
        m = truth_metrics(pred, truth, bpm=120.0)
        assert m["within_50ms_pct"] == 50.0 and m["within_250ms_pct"] == 100.0
        assert m["missed"] == 0 and m["false_positives"] == 0

    def test_wrong_label_is_a_miss_and_a_false_positive(self):
        m = truth_metrics([CuePoint("BUILD", 10.0, 0)], [{"label": "DROP", "time_sec": 10.0}], bpm=120.0)
        assert m["missed"] == 1 and m["false_positives"] == 1

    def test_more_than_a_bar_off_is_a_miss(self):
        m = truth_metrics([CuePoint("DROP", 15.0, 0)], [{"label": "DROP", "time_sec": 10.0}], bpm=120.0)
        assert m["missed"] == 1 and m["within_bar_pct"] == 0.0


class TestChorusAlignment:
    def phrases(self):
        # high mood: intro, up, chorus, chorus (same block), down, chorus
        return [Phrase(1.0, 1, 1), Phrase(9.0, 2, 1), Phrase(17.0, 5, 1),
                Phrase(25.0, 5, 1), Phrase(33.0, 3, 1), Phrase(41.0, 5, 1)]

    def test_consecutive_chorus_phrases_count_once(self):
        assert chorus_starts(make_grid(phrases=self.phrases())) == [17.0, 41.0]

    def test_non_high_mood_has_no_chorus(self):
        g = make_grid(phrases=[Phrase(17.0, 5, 2)])
        assert chorus_starts(g) == []
        assert drop_chorus_alignment([CuePoint("DROP", 17.0, 0)], g)["n_drops"] == 0

    def test_recall_and_precision(self):
        g = make_grid(phrases=self.phrases())
        cues = [CuePoint("DROP", 17.0, 0), CuePoint("DROP", 29.0, 0)]  # one on a chorus, one not
        r = drop_chorus_alignment(cues, g)
        assert (r["n_chorus"], r["chorus_hit"]) == (2, 1)
        assert (r["n_drops"], r["drop_on_chorus"]) == (2, 1)
