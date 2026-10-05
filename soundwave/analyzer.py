"""
Audio analysis pipeline: detects and beat-snaps all DJ-relevant hot cue points.

Cue types:
  INTRO   — track start / mix-in point (snapped to first downbeat)
  DROP    — up to MAX_DROPS high-energy drops, ranked by peak energy
  BUILD   — pre-drop buildup start, one per qualifying drop
  BREAK   — up to MAX_BREAKS breakdowns, ranked by depth
  VOCAL   — up to MAX_VOCAL_ENTRIES vocal entries, ranked by strength
  SECTION — up to MAX_SECTIONS generic structural changes (spectral novelty),
            for transitions the detectors above don't catch
  OUTRO32 — start of last 32 bars (exact, derived from tempo)
  OUTRO16 — start of last 16 bars (exact, derived from tempo)

Cue selection (see _select_by_importance): when there are more candidates
than fit in MAX_HOT_CUES, INTRO is always kept, remaining slots are filled
round-robin across DROP/SECTION/BUILD/VOCAL/BREAK (strongest first within
each type) so one prolific type can't crowd out the others, and OUTRO16/
OUTRO32 — structural rather than analyzed — only fill leftover slots.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import librosa
import numpy as np

from soundwave.config import (
    BREAK_ENERGY_PERCENTILE, BREAK_MERGE_GAP_SEC, BREAK_MIN_DURATION_SEC,
    BUILD_LOOKBACK_SEC, BUILD_MIN_SLOPE, BUILD_RISE_FRACTION,
    CONTENT_CUE_TYPES, CUE_COLORS,
    DROP_BASS_WEIGHT, DROP_ENERGY_PERCENTILE, DROP_ENERGY_PERCENTILE_FALLBACK,
    DROP_ENERGY_PERCENTILE_FALLBACK2, DROP_SMOOTH_SEC,
    DROP_MERGE_GAP_SEC, DROP_MIN_DURATION_SEC, MAX_BREAKS, MAX_DROPS,
    FRAME_HOP_SEC, MAX_HOT_CUES, MAX_SECTIONS, MAX_VOCAL_ENTRIES, OUTRO_BARS,
    SECTION_MIN_GAP_SEC, SECTION_NOVELTY_PERCENTILE,
    SNAP_TO_BEAT, SNAP_DROPS_TO_BARS, SNAP_OTHERS_TO_BEATS,
    VOCAL_ENERGY_FLOOR, VOCAL_ENERGY_PERCENTILE, VOCAL_MERGE_GAP_SEC, VOCAL_MIN_DURATION_SEC,
    VOCAL_SMOOTH_SEC,
)
from soundwave.rekordbox.models import CuePoint, TrackAnalysis


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _hop(sr: int) -> int:
    return max(1, int(sr * FRAME_HOP_SEC))


def _merge_intervals(starts: list[float], ends: list[float], gap: float):
    if not starts:
        return [], []
    ms, me = [starts[0]], [ends[0]]
    for s, e in zip(starts[1:], ends[1:]):
        if s - me[-1] <= gap:
            me[-1] = max(me[-1], e)
        else:
            ms.append(s)
            me.append(e)
    return ms, me


def _runs_above(signal: np.ndarray, threshold: float, min_frames: int):
    starts, ends = [], []
    in_run, run_start = False, 0
    for i, v in enumerate(signal):
        if v >= threshold and not in_run:
            in_run, run_start = True, i
        elif v < threshold and in_run:
            if i - run_start >= min_frames:
                starts.append(run_start)
                ends.append(i)
            in_run = False
    if in_run and len(signal) - run_start >= min_frames:
        starts.append(run_start)
        ends.append(len(signal))
    return starts, ends


def _runs_below(signal: np.ndarray, threshold: float, min_frames: int):
    return _runs_above(-signal, -threshold, min_frames)


def _band_energy(y: np.ndarray, sr: int, hop: int, low_hz: float, high_hz: float) -> np.ndarray:
    stft = np.abs(librosa.stft(y, hop_length=hop))
    freqs = librosa.fft_frequencies(sr=sr)
    mask = (freqs >= low_hz) & (freqs <= high_hz)
    band = stft[mask, :]
    return np.sqrt(np.mean(band ** 2, axis=0) + 1e-10)


# ---------------------------------------------------------------------------
# Beat grid
# ---------------------------------------------------------------------------

def _get_beat_times(y: np.ndarray, sr: int) -> tuple[float, np.ndarray]:
    """Return (bpm, beat_times_in_seconds)."""
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    bpm = float(np.atleast_1d(tempo)[0])
    beat_times = librosa.frames_to_time(beat_frames, sr=sr)
    return bpm, beat_times


def _snap_to_grid(time_sec: float, beat_times: np.ndarray, every_n_beats: int = 1) -> float:
    """Snap time_sec to the nearest beat (or every_n_beats-th beat)."""
    if len(beat_times) == 0:
        return time_sec
    grid = beat_times[::every_n_beats]
    idx = int(np.argmin(np.abs(grid - time_sec)))
    return float(grid[idx])


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------

def _find_drop_runs(combined: np.ndarray, percentile: float):
    """Returns None (not an empty result) whenever this percentile didn't
    pan out — either the ratio gate failed, or no run met
    DROP_MIN_DURATION_SEC — so callers cascading through several
    percentiles keep trying instead of stopping on an empty ([], [])."""
    threshold = float(np.percentile(combined, percentile))
    low_ref = float(np.percentile(combined, 10)) + 1e-10
    if threshold / low_ref < 1.5:
        return None

    min_frames = max(1, int(DROP_MIN_DURATION_SEC / FRAME_HOP_SEC))
    starts, ends = _runs_above(combined, threshold, min_frames)
    if not starts:
        return None
    starts_sec = [s * FRAME_HOP_SEC for s in starts]
    ends_sec = [e * FRAME_HOP_SEC for e in ends]
    return _merge_intervals(starts_sec, ends_sec, DROP_MERGE_GAP_SEC)


def detect_drops(y: np.ndarray, sr: int) -> List[CuePoint]:
    """Find up to MAX_DROPS high-energy drop sections, ranked by peak energy
    (strongest first — callers that need chronological order should sort
    explicitly; this order is preserved by cue selection so the strongest
    drops survive when a track has more candidates than fit in 8 hot cues).

    The energy signal is smoothed (DROP_SMOOTH_SEC) before thresholding —
    without it, a single quiet 100ms frame (silence between kick hits, a
    hi-hat trough) breaks an otherwise-solid run, so real, contiguous drop
    sections almost never survive DROP_MIN_DURATION_SEC of *uninterrupted*
    frames above the threshold.

    Retries at progressively lower percentiles if a stricter pass finds
    nothing — heavily mastered/compressed tracks (most modern pop/EDM,
    loudness-war mastering flattens dynamic range) otherwise never produce
    a single drop even after smoothing.
    """
    hop = _hop(sr)
    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    bass = _band_energy(y, sr, hop, 60, 250)
    combined = rms + DROP_BASS_WEIGHT * bass

    smooth_win = max(1, int(DROP_SMOOTH_SEC / FRAME_HOP_SEC))
    combined = np.convolve(combined, np.ones(smooth_win) / smooth_win, mode="same")

    result = None
    for percentile in (DROP_ENERGY_PERCENTILE, DROP_ENERGY_PERCENTILE_FALLBACK, DROP_ENERGY_PERCENTILE_FALLBACK2):
        result = _find_drop_runs(combined, percentile)
        if result is not None:
            break
    if result is None:
        return []
    starts_sec, ends_sec = result

    # Rank by peak energy within each run and keep top MAX_DROPS
    ranked = []
    for s, e in zip(starts_sec, ends_sec):
        sf = int(s / FRAME_HOP_SEC)
        ef = int(e / FRAME_HOP_SEC)
        peak = float(np.max(combined[sf:ef])) if sf < ef else 0.0
        ranked.append((peak, s))
    ranked.sort(reverse=True)
    top = ranked[:MAX_DROPS]

    return [CuePoint("DROP", t, CUE_COLORS["DROP"]) for _, t in top]


def detect_builds(y: np.ndarray, sr: int, drops: List[CuePoint]) -> List[CuePoint]:
    """Place BUILD at the point energy actually starts climbing within the
    lookback window, rather than a fixed offset before the drop — a build
    that kicks off 6s into a 16s lookback window previously still got
    flagged 16s early, landing in whatever came before it."""
    hop = _hop(sr)
    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    builds = []
    for drop in drops:
        end_f = int(drop.time_sec / FRAME_HOP_SEC)
        start_f = max(0, int((drop.time_sec - BUILD_LOOKBACK_SEC) / FRAME_HOP_SEC))
        window = rms[start_f:end_f]
        if len(window) < 4:
            continue
        slope, _ = np.polyfit(np.arange(len(window)), window, 1)
        if slope < BUILD_MIN_SLOPE:
            continue

        # Find where the window's energy first sustains above a point
        # BUILD_RISE_FRACTION of the way from its low to its high, rather
        # than assuming the rise starts right at the window's edge.
        lo, hi = float(window.min()), float(window.max())
        rise_level = lo + BUILD_RISE_FRACTION * (hi - lo)
        onset_offset = next((i for i, v in enumerate(window) if v >= rise_level), 0)
        t = max(0.0, (start_f + onset_offset) * FRAME_HOP_SEC)
        builds.append(CuePoint("BUILD", t, CUE_COLORS["BUILD"]))
    return builds


def detect_breakdowns(y: np.ndarray, sr: int) -> List[CuePoint]:
    """Find up to MAX_BREAKS low-energy breakdowns, ranked by depth (how far
    the dip falls below the track's high-energy reference) — strongest
    (deepest) first."""
    hop = _hop(sr)
    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    p25 = float(np.percentile(rms, BREAK_ENERGY_PERCENTILE))
    high_ref = float(np.percentile(rms, 90)) + 1e-10
    if p25 / high_ref > 0.67:
        return []
    threshold = max(p25 * 2.0, high_ref * 0.05)
    min_frames = max(1, int(BREAK_MIN_DURATION_SEC / FRAME_HOP_SEC))

    starts, ends = _runs_below(rms, threshold, min_frames)
    starts_sec = [s * FRAME_HOP_SEC for s in starts]
    ends_sec = [e * FRAME_HOP_SEC for e in ends]
    starts_sec, ends_sec = _merge_intervals(starts_sec, ends_sec, BREAK_MERGE_GAP_SEC)

    ranked = []
    for s, e in zip(starts_sec, ends_sec):
        sf = int(s / FRAME_HOP_SEC)
        ef = int(e / FRAME_HOP_SEC)
        avg = float(np.mean(rms[sf:ef])) if sf < ef else high_ref
        depth = high_ref - avg
        ranked.append((depth, s))
    ranked.sort(reverse=True)
    top = ranked[:MAX_BREAKS]

    return [CuePoint("BREAK", t, CUE_COLORS["BREAK"]) for _, t in top]


def detect_intro(drops: List[CuePoint], beat_times: np.ndarray) -> Optional[CuePoint]:
    """Place INTRO at the first downbeat (beat 0), or 2 bars before the
    earliest drop chronologically (drops may be strength-ordered, not
    time-ordered, so this can't assume drops[0] is first in time)."""
    if len(beat_times) == 0:
        return None
    if drops:
        first_drop_time = min(d.time_sec for d in drops)
        # Aim 2 bars (8 beats) before the first drop, snapped to a downbeat
        target = max(0.0, first_drop_time - (8 * (beat_times[1] - beat_times[0]) if len(beat_times) > 1 else 4.0))
        t = _snap_to_grid(target, beat_times, every_n_beats=4)
    else:
        t = float(beat_times[0])
    return CuePoint("INTRO", max(0.0, t), CUE_COLORS["INTRO"])


# torchcodec (used by demucs/torchaudio to load audio) ships loader shims
# tied to specific FFmpeg major versions, and Windows DLL resolution needs
# an exact match. A system FFmpeg newer than what the installed torchcodec
# build supports (e.g. FFmpeg 8 vs. torchcodec's newest tested tier) fails
# to load with an opaque "could not find module" error, silently disabling
# all vocal detection. If a known-compatible FFmpeg build is bundled here
# (see tools/README or setup.bat), prepend its bin dir to PATH for the
# Demucs subprocess only — this never touches the system-wide FFmpeg install.
_BUNDLED_FFMPEG_BIN = Path(__file__).resolve().parent.parent / "tools" / "ffmpeg-7.1.1-full_build-shared" / "bin"


def _demucs_env() -> dict:
    env = os.environ.copy()
    if _BUNDLED_FFMPEG_BIN.is_dir():
        env["PATH"] = str(_BUNDLED_FFMPEG_BIN) + os.pathsep + env.get("PATH", "")
    return env


def detect_vocal_entries(audio_path: str, sr: int) -> List[CuePoint]:
    """Find up to MAX_VOCAL_ENTRIES sustained vocal entries (verse/chorus
    starts, not just the first), ranked by how loud/sustained the entry is
    — strongest first — using Demucs stem separation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # -j spreads Demucs' chunked inference across all CPU cores. This
        # machine's torch build is CPU-only (no CUDA/XPU), so on the default
        # single job Demucs — by far the slowest step in the pipeline — only
        # used one core; this alone gives a multi-x speedup on a multi-core
        # machine at the cost of higher peak RAM use.
        jobs = str(max(1, os.cpu_count() or 1))
        result = subprocess.run(
            [sys.executable, "-m", "demucs", "--two-stems=vocals", "-j", jobs, "-o", tmpdir, audio_path],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=_demucs_env(),
        )
        if result.returncode != 0:
            print(f"  [warn] Demucs failed, skipping vocal detection.\n  {result.stderr[:300]}")
            return []

        vocal_files = list(Path(tmpdir).rglob("vocals.wav"))
        if not vocal_files:
            return []
        y_voc, _ = librosa.load(str(vocal_files[0]), sr=sr, mono=True)

    hop = _hop(sr)
    rms = librosa.feature.rms(y=y_voc, hop_length=hop)[0]
    min_frames = max(1, int(VOCAL_MIN_DURATION_SEC / FRAME_HOP_SEC))

    # Smoothed before thresholding — raw per-frame vocal energy dips between
    # words and breaths every 1-2s, which broke a continuous run long before
    # VOCAL_MIN_DURATION_SEC was reached (measured: on a real vocal track,
    # unsmoothed runs topped out around 1.3s, so a 4s-minimum entry could
    # never fire at all).
    smooth_win = max(1, int(VOCAL_SMOOTH_SEC / FRAME_HOP_SEC))
    smoothed = np.convolve(rms, np.ones(smooth_win) / smooth_win, mode="same")

    # Threshold relative to this track's own vocal-stem loudness — Demucs
    # stem levels vary a lot by mix/mastering, so a fixed absolute RMS
    # cutoff either misses quiet vocals or fires on bleed depending on the
    # track. A floor still guards against flagging a near-silent stem
    # (no vocals at all) purely from separation noise.
    threshold = max(float(np.percentile(smoothed, VOCAL_ENERGY_PERCENTILE)), VOCAL_ENERGY_FLOOR)
    starts, ends = _runs_above(smoothed, threshold, min_frames)
    starts_sec = [s * FRAME_HOP_SEC for s in starts]
    ends_sec = [e * FRAME_HOP_SEC for e in ends]
    starts_sec, ends_sec = _merge_intervals(starts_sec, ends_sec, VOCAL_MERGE_GAP_SEC)

    ranked = []
    for s, e in zip(starts_sec, ends_sec):
        sf = int(s / FRAME_HOP_SEC)
        ef = int(e / FRAME_HOP_SEC)
        strength = float(np.mean(rms[sf:ef])) if sf < ef else 0.0
        ranked.append((strength, s))
    ranked.sort(reverse=True)
    top = ranked[:MAX_VOCAL_ENTRIES]

    return [CuePoint("VOCAL", t, CUE_COLORS["VOCAL"]) for _, t in top]


def detect_sections(y: np.ndarray, sr: int, existing_times: List[float]) -> List[CuePoint]:
    """
    Find generic structural/transition moments via spectral novelty
    (onset-strength peaks), catching section changes — verse->chorus,
    breakdown->buildup — that the energy-percentile detectors above miss on
    tracks without a clean EDM-style drop.

    Candidates within SECTION_MIN_GAP_SEC of an already-detected cue (drop,
    build, break, vocal, intro) are skipped so this only adds *new*
    information rather than duplicating an existing flag.
    """
    hop = _hop(sr)
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    if len(onset_env) < 4:
        return []

    threshold = float(np.percentile(onset_env, SECTION_NOVELTY_PERCENTILE))
    wait_frames = max(1, int(SECTION_MIN_GAP_SEC / FRAME_HOP_SEC))
    delta = float(0.5 * onset_env.std())

    peaks = librosa.util.peak_pick(
        onset_env,
        pre_max=wait_frames, post_max=wait_frames,
        pre_avg=wait_frames, post_avg=wait_frames,
        delta=delta, wait=wait_frames,
    )

    candidates = [
        (float(onset_env[p]), float(p * FRAME_HOP_SEC))
        for p in peaks if onset_env[p] >= threshold
    ]
    candidates.sort(reverse=True)  # strongest novelty first

    sections: List[CuePoint] = []
    used_times = list(existing_times)
    for _, t in candidates:
        if any(abs(t - u) < SECTION_MIN_GAP_SEC for u in used_times):
            continue
        sections.append(CuePoint("SECTION", t, CUE_COLORS["SECTION"]))
        used_times.append(t)
        if len(sections) >= MAX_SECTIONS:
            break

    # Deliberately not sorted by time here — kept strongest-novelty-first so
    # cue selection can prioritize the most significant sections when a
    # track has more candidates than fit in MAX_HOT_CUES.
    return sections


def detect_outros(duration_sec: float, bpm: float) -> List[CuePoint]:
    """Place OUTRO32 and OUTRO16 cues based on bar length derived from BPM."""
    if bpm <= 0:
        return []
    beat_sec = 60.0 / bpm
    bar_sec = beat_sec * 4  # 4/4 time
    cues = []
    for bars in OUTRO_BARS:
        t = duration_sec - (bar_sec * bars)
        if t > 0:
            label = f"OUTRO{bars}"
            color = CUE_COLORS.get(label, 0xFF6400)
            cues.append(CuePoint(label, t, color))
    return cues


# ---------------------------------------------------------------------------
# Beat snapping
# ---------------------------------------------------------------------------

def _snap_cue(cue: CuePoint, beat_times: np.ndarray) -> CuePoint:
    if not SNAP_TO_BEAT or len(beat_times) == 0:
        return cue
    n = SNAP_DROPS_TO_BARS * 4 if cue.label == "DROP" else SNAP_OTHERS_TO_BEATS
    snapped = _snap_to_grid(cue.time_sec, beat_times, every_n_beats=n)
    return CuePoint(cue.label, max(0.0, snapped), cue.color)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze_track(audio_path: str, use_demucs: bool = True) -> TrackAnalysis:
    """
    Full analysis pipeline. Returns TrackAnalysis with beat-snapped CuePoints
    sorted by time and capped at MAX_HOT_CUES.
    """
    print(f"  Loading audio...")
    y, sr = librosa.load(audio_path, sr=None, mono=True)
    duration = librosa.get_duration(y=y, sr=sr)

    print(f"  Computing beat grid...")
    bpm, beat_times = _get_beat_times(y, sr)

    print(f"  Detecting drops...")
    drops = detect_drops(y, sr)

    print(f"  Detecting builds...")
    builds = detect_builds(y, sr, drops)

    print(f"  Detecting breakdowns...")
    breaks = detect_breakdowns(y, sr)

    print(f"  Detecting intro...")
    intro = detect_intro(drops, beat_times)

    print(f"  Detecting outros...")
    outros = detect_outros(duration, bpm)

    cues: List[CuePoint] = drops + builds + breaks + outros
    if intro:
        cues.append(intro)

    if use_demucs:
        print(f"  Detecting vocal entries (Demucs)...")
        cues.extend(detect_vocal_entries(audio_path, sr))

    print(f"  Detecting section changes...")
    existing_times = [c.time_sec for c in cues]
    cues.extend(detect_sections(y, sr, existing_times))

    # Snap all cues to the beat grid
    cues = [_snap_cue(c, beat_times) for c in cues]

    cues = _dedupe_and_cap(cues)

    title, artist = _read_tags(audio_path)
    return TrackAnalysis(
        audio_path=str(Path(audio_path).resolve()),
        title=title,
        artist=artist,
        duration_sec=duration,
        bpm=round(bpm, 2),
        cues=cues,
    )


# ---------------------------------------------------------------------------
# Analysis cache — protects against redoing (potentially hours of) analysis
# after the output XML is lost, overwritten, or otherwise clobbered.
# ---------------------------------------------------------------------------

def _cue_to_dict(c: CuePoint) -> dict:
    return {"label": c.label, "time_sec": c.time_sec, "color": c.color}


def _cue_from_dict(d: dict) -> CuePoint:
    return CuePoint(d["label"], d["time_sec"], d["color"])


def _analysis_to_dict(a: TrackAnalysis) -> dict:
    return {
        "audio_path": a.audio_path,
        "title": a.title,
        "artist": a.artist,
        "duration_sec": a.duration_sec,
        "bpm": a.bpm,
        "cues": [_cue_to_dict(c) for c in a.cues],
    }


def _analysis_from_dict(d: dict) -> TrackAnalysis:
    return TrackAnalysis(
        audio_path=d["audio_path"],
        title=d["title"],
        artist=d["artist"],
        duration_sec=d["duration_sec"],
        bpm=d["bpm"],
        cues=[_cue_from_dict(c) for c in d["cues"]],
    )


def load_analysis_cache(cache_path: Path) -> dict:
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_analysis_cache(cache_path: Path, cache: dict) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def _cache_key(audio_path: str, use_demucs: bool) -> str:
    """Resolved path + mtime + demucs flag, so a changed file or a switch
    between --no-lyrics and vocal-detection mode invalidates the entry
    instead of silently reusing cues computed under different settings."""
    resolved = str(Path(audio_path).resolve())
    mtime = Path(audio_path).stat().st_mtime
    return f"{resolved}::{mtime}::demucs={use_demucs}"


def analyze_track_cached(
    audio_path: str, cache: dict, *, use_demucs: bool = True,
) -> Tuple[TrackAnalysis, bool]:
    """
    Like analyze_track, but checks `cache` first and stores the result back
    into it on a miss (mutated in place — caller decides when to persist to
    disk, e.g. after every track so an interrupted run loses nothing).

    Returns (analysis, was_cached).
    """
    key = _cache_key(audio_path, use_demucs)
    if key in cache:
        return _analysis_from_dict(cache[key]), True

    result = analyze_track(audio_path, use_demucs=use_demucs)
    cache[key] = _analysis_to_dict(result)
    return result, False


def _dedupe_within_label(cues: List[CuePoint]) -> List[CuePoint]:
    """Drop near-duplicates (<1s apart) within a single label's candidate
    list, keeping earlier-in-list entries — each detector already orders
    its own candidates strongest-first, so this keeps the stronger of any
    two near-duplicates rather than just whichever comes first in time."""
    kept: List[CuePoint] = []
    for cue in cues:
        if any(abs(cue.time_sec - k.time_sec) < 1.0 for k in kept):
            continue
        kept.append(cue)
    return kept


def _select_by_importance(by_label: Dict[str, List[CuePoint]]) -> List[CuePoint]:
    """
    Choose MAX_HOT_CUES cues when there are more candidates than that.

    INTRO is always kept. Remaining slots are filled round-robin across
    CONTENT_CUE_TYPES (DROP, SECTION, BUILD, VOCAL, BREAK) — one per type
    per round, strongest-first within each type (each detector already
    ranks its own candidates that way) — so a track with e.g. 4 drops can't
    crowd out its only build or vocal entry. OUTRO16/OUTRO32 are structural
    navigation aids rather than analyzed content, so they only fill
    genuinely leftover slots, preferring OUTRO16 (closer to the actual end,
    more actionable) when just one fits.
    """
    selected: List[CuePoint] = []
    intro = by_label.get("INTRO", [None])[0]
    if intro:
        selected.append(intro)

    budget = MAX_HOT_CUES - len(selected)
    queues = {label: list(by_label.get(label, [])) for label in CONTENT_CUE_TYPES}

    progressed = True
    while budget > 0 and progressed:
        progressed = False
        for label in CONTENT_CUE_TYPES:
            if budget <= 0:
                break
            if queues[label]:
                selected.append(queues[label].pop(0))
                budget -= 1
                progressed = True

    for cue in by_label.get("OUTRO16", []) + by_label.get("OUTRO32", []):
        if budget <= 0:
            break
        selected.append(cue)
        budget -= 1

    selected.sort(key=lambda c: c.time_sec)
    return selected


def _dedupe_and_cap(cues: List[CuePoint]) -> List[CuePoint]:
    by_label: Dict[str, List[CuePoint]] = {}
    for cue in cues:
        by_label.setdefault(cue.label, []).append(cue)

    for label in by_label:
        by_label[label] = _dedupe_within_label(by_label[label])

    total = sum(len(v) for v in by_label.values())
    if total <= MAX_HOT_CUES:
        flat = [c for cue_list in by_label.values() for c in cue_list]
        flat.sort(key=lambda c: c.time_sec)
        return flat

    return _select_by_importance(by_label)


def _read_tags(audio_path: str):
    try:
        from mutagen import File as MutagenFile
        tags = MutagenFile(audio_path, easy=True)
        if tags:
            title = str(tags.get("title", [Path(audio_path).stem])[0])
            artist = str(tags.get("artist", ["Unknown"])[0])
            return title, artist
    except Exception:
        pass
    return Path(audio_path).stem, "Unknown"
