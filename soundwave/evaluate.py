"""
Cue-placement accuracy metrics.

Two kinds, because "is this cue in the right place" has two different answers:

1. Reference metrics (no hand labelling needed) — how a cue relates to the
   grid and phrase boundaries Rekordbox itself computed for the track:
   is it on a beat, on a bar start, on a phrase start? These expose grid
   errors precisely but can't tell you the *musical* section is right.

2. Ground-truth metrics — compare predicted cues with cue positions a person
   marked as correct (a JSON file; see `truth_template`). This is the only
   measure of true accuracy; keep a few dozen hand-checked tracks around and
   re-run it after every detector change.

All times are seconds unless a name says otherwise.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

import numpy as np

from soundwave.rekordbox.anlz_grid import RekordboxGrid
from soundwave.rekordbox.models import CuePoint

ON_GRID_TOL_MS = 30.0      # within this of a beat/bar/phrase start counts as "on" it
ON_PHRASE_BEATS = 0.1      # "on" a phrase start = within a tenth of a beat
NEAR_PHRASE_BEATS = 8.0    # "near a phrase boundary" = within 2 bars


@dataclass
class CueRef:
    """One cue measured against Rekordbox's analysis of the same track."""
    label: str
    beat_off_ms: float             # distance to nearest Rekordbox beat
    downbeat_off_ms: float         # distance to nearest Rekordbox bar start
    phrase_off_beats: Optional[float]  # distance to nearest phrase start; None if Rekordbox has none


def _nearest_dist(t: float, times: np.ndarray) -> float:
    return float(np.min(np.abs(times - t))) if len(times) else float("inf")


def cue_reference_metrics(cues: Iterable[CuePoint], grid: RekordboxGrid) -> List[CueRef]:
    downbeats = grid.downbeat_times
    phrase_times = np.array([p.time_sec for p in grid.phrases])
    beat_sec = 60.0 / grid.bpm
    out = []
    for c in cues:
        out.append(CueRef(
            label=c.label,
            beat_off_ms=_nearest_dist(c.time_sec, grid.beat_times) * 1000,
            downbeat_off_ms=_nearest_dist(c.time_sec, downbeats) * 1000,
            phrase_off_beats=(_nearest_dist(c.time_sec, phrase_times) / beat_sec) if len(phrase_times) else None,
        ))
    return out


def summarize_reference(refs: List[CueRef]) -> Dict[str, dict]:
    """Per-label and overall ('ALL') roll-up of reference metrics."""
    groups: Dict[str, List[CueRef]] = defaultdict(list)
    for r in refs:
        groups[r.label].append(r)
        groups["ALL"].append(r)

    def pct(xs: List[bool]) -> Optional[float]:
        return round(100.0 * sum(xs) / len(xs), 1) if xs else None

    summary = {}
    for label, rs in groups.items():
        with_phrase = [r for r in rs if r.phrase_off_beats is not None]
        summary[label] = {
            "n": len(rs),
            "on_beat_pct": pct([r.beat_off_ms <= ON_GRID_TOL_MS for r in rs]),
            "on_downbeat_pct": pct([r.downbeat_off_ms <= ON_GRID_TOL_MS for r in rs]),
            "median_downbeat_off_ms": round(statistics.median(r.downbeat_off_ms for r in rs), 1),
            "on_phrase_pct": pct([r.phrase_off_beats <= ON_PHRASE_BEATS for r in with_phrase]),
            "near_phrase_pct": pct([r.phrase_off_beats <= NEAR_PHRASE_BEATS for r in with_phrase]),
            "n_with_phrases": len(with_phrase),
        }
    return summary


@dataclass
class GridAgreement:
    """How a tracker's own beat grid compares with Rekordbox's for one track."""
    median_offset_ms: float   # signed: positive = tracker beats are LATE vs Rekordbox
    bar_phase_ok_pct: float   # % of tracker "downbeats" (every 4th beat) that are Rekordbox bar starts


def grid_agreement(tracker_beats: np.ndarray, grid: RekordboxGrid) -> Optional[GridAgreement]:
    if len(tracker_beats) < 8:
        return None
    rb = grid.beat_times
    idx = np.clip(np.searchsorted(rb, tracker_beats), 1, len(rb) - 1)
    # nearest of the two neighbours
    prev_closer = np.abs(tracker_beats - rb[idx - 1]) <= np.abs(tracker_beats - rb[idx])
    nearest = np.where(prev_closer, idx - 1, idx)
    offsets = (tracker_beats - rb[nearest]) * 1000.0
    assumed_downbeats = nearest[::4]  # the old code treated every 4th tracker beat as a bar start
    phase_ok = grid.beat_numbers[assumed_downbeats] == 1
    return GridAgreement(
        median_offset_ms=float(np.median(offsets)),
        bar_phase_ok_pct=float(100.0 * np.mean(phase_ok)),
    )


# ---------------------------------------------------------------------------
# Drops vs Rekordbox choruses
# ---------------------------------------------------------------------------

HIGH_MOOD = 1
# Rekordbox's high-mood phrase set: 1 Intro, 2 Up, 3 Down, 5 Chorus, 6 Outro.
UP, DOWN, CHORUS = 2, 3, 5
CHORUS_KIND = CHORUS


def phrase_starts(grid: RekordboxGrid, *kinds: int) -> List[float]:
    """Start of each block of phrases of the given kind(s) (consecutive ones
    of the same kind count once). Only high-mood phrase sets are
    interpreted; others return []."""
    out, prev = [], None
    for p in grid.phrases:
        if p.mood == HIGH_MOOD and p.kind in kinds and prev != (p.mood, p.kind):
            out.append(p.time_sec)
        prev = (p.mood, p.kind)
    return out


def chorus_starts(grid: RekordboxGrid) -> List[float]:
    return phrase_starts(grid, CHORUS)


def pre_chorus_ups(grid: RekordboxGrid) -> List[float]:
    """Start of the Up phrase that runs straight into a Chorus — Rekordbox's
    version of "the build before the drop". (Rekordbox also marks many
    other Ups mid-section, which aren't builds in the DJ sense.)"""
    ph = grid.phrases
    return [q.time_sec for i, q in enumerate(ph[:-1])
            if q.mood == HIGH_MOOD and q.kind == UP and ph[i + 1].kind == CHORUS]


# label -> (what it is compared with, Rekordbox phrase starts for a grid)
PHRASE_PROXY = {
    "DROP": ("chorus", lambda g: phrase_starts(g, CHORUS)),
    "BUILD": ("up->chorus", pre_chorus_ups),
    # Rekordbox files a kick-leaves-and-rises section under Up as often as Down.
    "BREAK": ("up/down", lambda g: phrase_starts(g, UP, DOWN)),
}


def cue_phrase_alignment(
    cues: Iterable[CuePoint], grid: RekordboxGrid, label: str, tol_bars: float = 1.0,
) -> dict:
    """How well `label` cues line up with the matching Rekordbox phrase
    starts (PHRASE_PROXY), within `tol_bars` bars. Rekordbox's phrases are
    themselves a heuristic, so read this as agreement with an independent
    detector, not as ground truth.

    phrase_hit: phrases that have a cue nearby (recall).
    cue_on_phrase: cues that sit at such a phrase start (precision).
    """
    phrases = np.array(PHRASE_PROXY[label][1](grid))
    times = np.array([c.time_sec for c in cues if c.label == label])
    tol = tol_bars * 4 * 60.0 / grid.bpm
    return {
        "n_phrase": len(phrases),
        "phrase_hit": int(sum(np.any(np.abs(times - p) <= tol) for p in phrases)) if len(times) else 0,
        "n_cues": len(times) if len(phrases) else 0,
        "cue_on_phrase": int(sum(np.any(np.abs(phrases - t) <= tol) for t in times)) if len(phrases) else 0,
    }


def drop_chorus_alignment(cues: Iterable[CuePoint], grid: RekordboxGrid, tol_bars: float = 1.0) -> dict:
    r = cue_phrase_alignment(cues, grid, "DROP", tol_bars)
    return {"n_chorus": r["n_phrase"], "chorus_hit": r["phrase_hit"],
            "n_drops": r["n_cues"], "drop_on_chorus": r["cue_on_phrase"]}


# ---------------------------------------------------------------------------
# Ground truth
# ---------------------------------------------------------------------------

def truth_template(cues: Iterable[CuePoint]) -> List[dict]:
    """Predicted cues as an editable list: fix the times (and delete/add
    entries) until it reflects where the cues *should* be."""
    return [{"label": c.label, "time_sec": round(c.time_sec, 3)} for c in cues]


def truth_metrics(
    predicted: List[CuePoint], truth: List[dict], bpm: float,
    tolerances_sec: tuple = (0.05, 0.25),
) -> dict:
    """For each truth cue: error to the nearest predicted cue *of the same
    label*. Reports hit rates at fixed tolerances plus within-one-bar, how
    many truth cues were missed outright, and how many predictions matched
    nothing (false positives)."""
    bar_sec = 4 * 60.0 / bpm if bpm > 0 else 2.0
    errors: List[Optional[float]] = []
    used = set()
    for tc in truth:
        same = [(abs(p.time_sec - tc["time_sec"]), i) for i, p in enumerate(predicted) if p.label == tc["label"]]
        if not same:
            errors.append(None)
            continue
        err, i = min(same)
        errors.append(err)
        if err <= bar_sec:
            used.add(i)

    matched = [e for e in errors if e is not None]
    n = len(truth)
    result = {
        "n_truth": n,
        "missed": sum(e is None or e > bar_sec for e in errors),
        "false_positives": len(predicted) - len(used),
        "within_bar_pct": round(100.0 * sum(e <= bar_sec for e in matched) / n, 1) if n else None,
        "median_abs_err_sec": round(statistics.median(matched), 3) if matched else None,
    }
    for tol in tolerances_sec:
        result[f"within_{int(tol * 1000)}ms_pct"] = (
            round(100.0 * sum(e <= tol for e in matched) / n, 1) if n else None
        )
    return result
