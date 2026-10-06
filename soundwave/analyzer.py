"""
Audio analysis pipeline: detects and beat-snaps all DJ-relevant hot cue points.

Cue types:
  INTRO   — track start / mix-in point (first downbeat once the audio begins)
  DROP    — up to MAX_DROPS drops: where the kick returns after a gap and the
            mix gets louder (drum stem, needs Demucs); falls back to the
            loudest sustained sections when there are no stems or no such gap
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

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import librosa
import numpy as np

from soundwave.config import (
    BREAK_ENERGY_PERCENTILE, BREAK_MERGE_GAP_SEC, BREAK_MIN_DURATION_SEC,
    BUILD_LOOKBACK_SEC, BUILD_MIN_SLOPE, BUILD_RISE_FRACTION,
    CONTENT_CUE_TYPES, CUE_COLORS, DOWNBEAT_SNAP_LABELS,
    DROP_BASS_WEIGHT, DROP_ENERGY_PERCENTILE, DROP_ENERGY_PERCENTILE_FALLBACK,
    DROP_ENERGY_PERCENTILE_FALLBACK2, DROP_SMOOTH_SEC,
    DRUM_GAP_MIN_SEC, DRUM_HOP, DRUM_KICK_HIGH_HZ, DRUM_KICK_LOW_HZ, DRUM_ONSET_FRACTION,
    DRUM_PRESENCE_FRACTION, DRUM_PRESENCE_SMOOTH_SEC,
    DROP_CONTRAST_AFTER_SEC, DROP_CONTRAST_BEFORE_SEC, DROP_MIN_CONTRAST,
    BUILD_BAND_HIGH_HZ, BUILD_BAND_LOW_HZ, BUILD_MAX_SEC, BUILD_MIN_RISE_DB, BUILD_MIN_SEC,
    BUILD_PEAK_MAX_GAP_SEC, BUILD_SMOOTH_SEC,
    DROP_MERGE_GAP_SEC, DROP_MIN_DURATION_SEC, MAX_BREAKS, MAX_DROPS,
    FRAME_HOP_SEC, MAX_HOT_CUES, MAX_SECTIONS, MAX_VOCAL_ENTRIES, OUTRO_BARS,
    SECTION_MIN_GAP_SEC, SECTION_MIN_START_SEC, SECTION_NOVELTY_PERCENTILE,
    SNAP_TO_BEAT, SNAP_DROPS_TO_BARS, SNAP_OTHERS_TO_BEATS,
    VOCAL_ENERGY_FLOOR, VOCAL_ENERGY_PERCENTILE, VOCAL_MERGE_GAP_SEC, VOCAL_MIN_DURATION_SEC,
    VOCAL_SMOOTH_SEC,
)
from soundwave.rekordbox.anlz_grid import RekordboxIndex
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


@dataclass
class BeatGrid:
    """The grid cues are snapped to. `downbeat_times` is None when bar phase
    is unknown (librosa's tracker has no downbeat concept), in which case
    bar-level snapping falls back to every-4th-beat from an arbitrary start."""
    beat_times: np.ndarray
    downbeat_times: Optional[np.ndarray]
    bpm: float
    source: str  # "rekordbox" | "librosa"


def _resolve_grid(
    audio_path: str, y: np.ndarray, sr: int, rb_index: Optional[RekordboxIndex],
) -> BeatGrid:
    """Prefer Rekordbox's own analysis of this exact file — it's the grid the
    user sees (and may have hand-corrected), and carries real bar positions —
    and only fall back to librosa for tracks Rekordbox hasn't analysed."""
    rb = rb_index.grid_for(audio_path) if rb_index is not None else None
    if rb is not None:
        return BeatGrid(rb.beat_times, rb.downbeat_times, rb.bpm, "rekordbox")
    bpm, beat_times = _get_beat_times(y, sr)
    return BeatGrid(beat_times, None, bpm, "librosa")


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
    # A quiet stretch that starts at frame 0 is the intro, not a breakdown:
    # there was no music yet for it to break down from.
    keep = [k for k, st in enumerate(starts) if st > 0]
    starts, ends = [starts[k] for k in keep], [ends[k] for k in keep]
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


def _audio_start_sec(y: np.ndarray, sr: int, top_db: float = 40.0) -> float:
    """Where the audio actually begins: skips digital silence and the
    near-silent lead-in many files have (e.g. MP3 encoder padding)."""
    if len(y) == 0:
        return 0.0
    _, (start, _) = librosa.effects.trim(y, top_db=top_db)
    return float(start) / sr


def detect_intro(
    beat_times: np.ndarray,
    downbeat_times: Optional[np.ndarray] = None,
    audio_start_sec: float = 0.0,
) -> Optional[CuePoint]:
    """INTRO is the track start / mix-in point: the first bar line once the
    audio begins. With real downbeats (Rekordbox grid) it is an actual bar
    start; without them it is the first beat, whose bar phase is a guess.

    It deliberately does not depend on where the drop is — a cue "2 bars
    before the drop" is a pre-drop cue, not the start of the track, and on
    tracks with a long intro landed minutes into the song.
    """
    has_downbeats = downbeat_times is not None and len(downbeat_times) > 0
    grid = downbeat_times if has_downbeats else beat_times
    if len(grid) == 0:
        return None
    # A downbeat landing on the very first attack counts (small allowance for
    # the attack sitting a few ms before the grid line).
    after = grid[grid >= audio_start_sec - 0.1]
    t = float(after[0]) if len(after) else float(grid[0])
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


@dataclass
class Stems:
    vocals: np.ndarray
    drums: np.ndarray


def _stem_cache_dir(cache_dir: Path, audio_path: str) -> Path:
    p = Path(audio_path).resolve()
    key = hashlib.sha1(f"{p}::{p.stat().st_mtime_ns}".encode("utf-8")).hexdigest()[:16]
    return cache_dir / key


def separate_stems(audio_path: str, sr: int, cache_dir: Optional[Path] = None) -> Optional[Stems]:
    """Run Demucs once and return the vocal and drum stems (mono, at `sr`).

    The model separates all four stems regardless; asking for just two only
    sums the rest, so getting drums alongside vocals costs nothing extra.
    With `cache_dir`, the two stems are kept on disk so re-running (e.g. the
    evaluation harness comparing detector changes) skips the ~1x-realtime
    separation.
    """
    cached = _stem_cache_dir(cache_dir, audio_path) if cache_dir else None
    if cached is not None and (cached / "vocals.wav").exists() and (cached / "drums.wav").exists():
        return Stems(
            vocals=librosa.load(str(cached / "vocals.wav"), sr=sr, mono=True)[0],
            drums=librosa.load(str(cached / "drums.wav"), sr=sr, mono=True)[0],
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        # -j spreads Demucs' chunked inference across all CPU cores. This
        # machine's torch build is CPU-only (no CUDA/XPU), so on the default
        # single job Demucs — by far the slowest step in the pipeline — only
        # used one core; this alone gives a multi-x speedup on a multi-core
        # machine at the cost of higher peak RAM use.
        jobs = str(max(1, os.cpu_count() or 1))
        result = subprocess.run(
            [sys.executable, "-m", "demucs", "-j", jobs, "-o", tmpdir, audio_path],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=_demucs_env(),
        )
        if result.returncode != 0:
            print(f"  [warn] Demucs failed, skipping vocal/drum detection.\n  {result.stderr[:300]}")
            return None

        vocal_files = list(Path(tmpdir).rglob("vocals.wav"))
        drum_files = list(Path(tmpdir).rglob("drums.wav"))
        if not vocal_files or not drum_files:
            return None
        if cached is not None:
            cached.mkdir(parents=True, exist_ok=True)
            shutil.copy(vocal_files[0], cached / "vocals.wav")
            shutil.copy(drum_files[0], cached / "drums.wav")
        return Stems(
            vocals=librosa.load(str(vocal_files[0]), sr=sr, mono=True)[0],
            drums=librosa.load(str(drum_files[0]), sr=sr, mono=True)[0],
        )


def detect_vocal_entries(audio_path: str, sr: int, stems: Optional[Stems] = None) -> List[CuePoint]:
    """Find up to MAX_VOCAL_ENTRIES sustained vocal entries (verse/chorus
    starts, not just the first), ranked by how loud/sustained the entry is
    — strongest first — using Demucs stem separation (or `stems` if the
    caller already separated this track)."""
    stems = stems or separate_stems(audio_path, sr)
    if stems is None:
        return []
    y_voc = stems.vocals

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


def _smooth_edge(x: np.ndarray, n: int) -> np.ndarray:
    """Moving average that doesn't dip at the edges (zero-padded 'same'
    convolution would fake a quiet gap at the start and end of the track)."""
    if n <= 1:
        return x
    pad = n // 2
    padded = np.pad(x, (pad, n - 1 - pad), mode="edge")
    return np.convolve(padded, np.ones(n) / n, mode="valid")


@dataclass
class KickStructure:
    """Where the kick is and isn't, from the drum stem — the skeleton that
    drops, breaks and builds are all read from. Frame indices use `fh`
    seconds per frame."""
    kick: np.ndarray            # kick-band energy per frame (unsmoothed)
    mix: np.ndarray             # full-mix RMS per frame
    fh: float
    gaps: List[Tuple[int, int]]  # kick-free stretches >= DRUM_GAP_MIN_SEC, as (start, end) frames

    @property
    def n(self) -> int:
        return len(self.kick)


def analyze_kick_structure(y: np.ndarray, drums: np.ndarray, sr: int) -> Optional[KickStructure]:
    """Locate kick-free gaps in the drum stem. Kick presence is measured on
    the stem's low band because breakdowns often keep hats and percussion
    going and only drop the kick. None if the stem has no kick at all."""
    hop = DRUM_HOP
    fh = hop / sr
    kick = _band_energy(drums, sr, hop, DRUM_KICK_LOW_HZ, DRUM_KICK_HIGH_HZ)
    mix = librosa.feature.rms(y=y, hop_length=hop)[0]
    n = min(len(kick), len(mix))
    kick, mix = kick[:n], mix[:n]
    if n < 4:
        return None
    ref = float(np.percentile(kick, 90))
    if ref <= 1e-6:
        return None
    present = _smooth_edge(kick, max(1, int(DRUM_PRESENCE_SMOOTH_SEC / fh))) >= DRUM_PRESENCE_FRACTION * ref
    min_gap = max(1, int(DRUM_GAP_MIN_SEC / fh))
    starts, ends = _runs_above(~present, 0.5, min_gap)  # bool array: True frames are kick-free
    return KickStructure(kick, mix, fh, list(zip(starts, ends)))


def drops_from_structure(ks: KickStructure) -> List[CuePoint]:
    """Drops are where the kick returns after a gap, provided the mix gets
    louder when it does — the structure DJs mean by "the drop", rather than
    "wherever it's loudest" (which fires on loud intros, choruses and
    fills).

    Returns DROP cues ranked strongest-first (by how much louder the mix is
    after the kick returns than before), unsnapped. The time is the first
    real kick, not the start of the drum run, so kickless snare/hat
    pickups before the drop don't pull the cue early.
    """
    fh, kick, mix, n = ks.fh, ks.kick, ks.mix, ks.n
    before_f = max(1, int(DROP_CONTRAST_BEFORE_SEC / fh))
    after_f = max(1, int(DROP_CONTRAST_AFTER_SEC / fh))
    lookahead_f = max(1, int(2.0 / fh))

    ranked = []
    for start, end in ks.gaps:
        if end >= n:
            continue  # gap runs to the end of the track: an outro, not a drop
        # Compare with the gap as a whole, not just its last moments: a loud
        # riser right before the drop would otherwise hide the contrast.
        before = float(np.mean(mix[max(start, end - before_f):end])) + 1e-10
        after = float(np.mean(mix[end:end + after_f]))
        contrast = after / before
        if contrast < DROP_MIN_CONTRAST:
            continue

        # Refine on unsmoothed frames: first kick that reaches a fraction of
        # the upcoming peak. Start a little early since smoothing blurred the edge.
        lo = max(0, end - int(0.5 / fh))
        peak = float(np.max(kick[end:end + lookahead_f]))
        hits = np.nonzero(kick[lo:end + lookahead_f] >= DRUM_ONSET_FRACTION * peak)[0]
        onset = lo + int(hits[0]) if len(hits) else end
        ranked.append((contrast, onset * fh))

    ranked.sort(reverse=True)
    return [CuePoint("DROP", t, CUE_COLORS["DROP"]) for _, t in ranked[:MAX_DROPS]]


def detect_drum_drops(y: np.ndarray, drums: np.ndarray, sr: int) -> List[CuePoint]:
    ks = analyze_kick_structure(y, drums, sr)
    return drops_from_structure(ks) if ks is not None else []


def breaks_from_structure(ks: KickStructure, beat_sec: float) -> List[CuePoint]:
    """A breakdown starts where the kick leaves and the track comes back
    (kick returns later). Not the intro (no kick yet, so nothing "left")
    and not the outro (the kick never returns).

    The cue is the first beat with no kick: the last kick's onset plus one
    beat, which bar-snapping then moves to the bar line. Ranked longest gap
    first (a deeper, longer breakdown matters more to a DJ).
    """
    fh, kick, n = ks.fh, ks.kick, ks.n
    ranked = []
    for start, end in ks.gaps:
        if start <= 0 or end >= n:
            continue
        # Last kick before the gap: the final thump above a fraction of the
        # level the kick was playing at, then walk back to its onset.
        w0 = max(0, start - int(4.0 / fh))
        level = float(np.percentile(kick[w0:start + 1], 90)) if start > w0 else 0.0
        thr = DRUM_ONSET_FRACTION * level
        hi = min(n, start + int(1.0 / fh))
        above = np.nonzero(kick[w0:hi] >= thr)[0]
        if not len(above):
            continue
        i = w0 + int(above[-1])
        while i > w0 and kick[i - 1] >= thr:
            i -= 1
        ranked.append(((end - start) * fh, i * fh + beat_sec))

    ranked.sort(reverse=True)
    return [CuePoint("BREAK", t, CUE_COLORS["BREAK"]) for _, t in ranked[:MAX_BREAKS]]


def builds_from_structure(
    y: np.ndarray, sr: int, ks: KickStructure, drops: List[CuePoint], bar_sec: float = 0.0,
) -> List[CuePoint]:
    """For each drop, find the climb in the high band (2-10 kHz, in dB so
    track loudness doesn't matter) that leads into it, and place BUILD where
    that climb starts.

    The climb is traced backwards from its peak: it starts where the level
    last stays above BUILD_RISE_FRACTION of the way up from the window's low.
    It counts only if it rises >= BUILD_MIN_RISE_DB over >= BUILD_MIN_SEC and
    peaks close to the drop — a loud passage long before the drop isn't a
    build. Windows never reach back past the previous drop, so one drop's
    aftermath can't be read as the next one's build.

    Given `bar_sec`, the onset is rounded to a whole number of 4-bar phrases
    before the drop (minimum one): the raw climb start is noisy, but dance
    music builds run in phrases and end exactly on the drop.
    """
    hi = _band_energy(y, sr, DRUM_HOP, BUILD_BAND_LOW_HZ, BUILD_BAND_HIGH_HZ)[:ks.n]
    env = 20.0 * np.log10(_smooth_edge(hi, max(1, int(BUILD_SMOOTH_SEC / ks.fh))) + 1e-10)
    fh = ks.fh
    ordered = sorted(d.time_sec for d in drops)

    builds = []
    for i, t in enumerate(ordered):
        d = min(ks.n, int(t / fh))
        floor = int((ordered[i - 1] + 8.0) / fh) if i > 0 else 0
        a = max(floor, d - int(BUILD_MAX_SEC / fh), 0)
        if d - a < int(BUILD_MIN_SEC / fh):
            continue
        window = env[a:d]
        peak = int(np.argmax(window))
        if (len(window) - peak) * fh > BUILD_PEAK_MAX_GAP_SEC:
            continue
        low = float(window[:peak + 1].min())
        rise = float(window[peak]) - low
        if rise < BUILD_MIN_RISE_DB:
            continue
        thr = low + BUILD_RISE_FRACTION * rise
        j = peak
        while j > 0 and window[j - 1] >= thr:
            j -= 1
        if (peak - j) * fh < BUILD_MIN_SEC:
            continue
        onset = (a + j) * fh
        if bar_sec > 0:
            phrases = max(1, round((t - onset) / (4 * bar_sec)))
            onset = t - phrases * 4 * bar_sec
        builds.append(CuePoint("BUILD", max(0.0, onset), CUE_COLORS["BUILD"]))
    return builds


def _drop_builds_on_breaks(builds: List[CuePoint], breaks: List[CuePoint], bar_sec: float) -> List[CuePoint]:
    """A BUILD within a bar of a BREAK is the same moment (a short gap that is
    both the breakdown and the build); keep the BREAK, free the slot."""
    return [b for b in builds if all(abs(b.time_sec - k.time_sec) > bar_sec for k in breaks)]


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
        if t < SECTION_MIN_START_SEC:
            continue  # an onset peak at the very start is the track starting, not a section change
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

def _snap_cue(
    cue: CuePoint, beat_times: np.ndarray, downbeat_times: Optional[np.ndarray] = None,
) -> CuePoint:
    if not SNAP_TO_BEAT or len(beat_times) == 0:
        return cue
    if (downbeat_times is not None and len(downbeat_times) > 0
            and cue.label in DOWNBEAT_SNAP_LABELS):
        # Structural cues start on a bar. With real downbeats this is exact;
        # DROP may be coarsened to every N bars via SNAP_DROPS_TO_BARS.
        n = SNAP_DROPS_TO_BARS if cue.label == "DROP" else 1
        snapped = _snap_to_grid(cue.time_sec, downbeat_times, every_n_beats=n)
        return CuePoint(cue.label, max(0.0, snapped), cue.color)
    n = SNAP_DROPS_TO_BARS * 4 if cue.label == "DROP" else SNAP_OTHERS_TO_BEATS
    snapped = _snap_to_grid(cue.time_sec, beat_times, every_n_beats=n)
    return CuePoint(cue.label, max(0.0, snapped), cue.color)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze_track(
    audio_path: str, use_demucs: bool = True, rb_index: Optional[RekordboxIndex] = None,
    stems: Optional[Stems] = None, stem_cache_dir: Optional[Path] = None,
) -> TrackAnalysis:
    """
    Full analysis pipeline. Returns TrackAnalysis with beat-snapped CuePoints
    sorted by time and capped at MAX_HOT_CUES. If `rb_index` knows this file,
    cues snap to Rekordbox's own beat grid and bar positions. With Demucs
    on (or `stems` supplied), drops come from the drum stem's kick returns,
    falling back to the loudness detector when the track has none.
    """
    print(f"  Loading audio...")
    y, sr = librosa.load(audio_path, sr=None, mono=True)
    duration = librosa.get_duration(y=y, sr=sr)

    print(f"  Computing beat grid...")
    grid = _resolve_grid(audio_path, y, sr, rb_index)
    bpm, beat_times = grid.bpm, grid.beat_times
    print(f"  Beat grid: {grid.source} ({bpm:.2f} BPM)")

    if use_demucs and stems is None:
        print(f"  Separating stems (Demucs)...")
        stems = separate_stems(audio_path, sr, stem_cache_dir)

    ks = analyze_kick_structure(y, stems.drums, sr) if stems is not None else None

    print(f"  Detecting drops...")
    drops = drops_from_structure(ks) if ks is not None else []
    from_drums = bool(drops)
    if not drops:
        drops = detect_drops(y, sr)

    # Builds and breaks come from the same kick structure as the drops; the
    # loudness-based detectors remain the fallback when there's none to read.
    print(f"  Detecting builds...")
    bar_sec = 240.0 / bpm if bpm > 0 else 0.0
    builds = builds_from_structure(y, sr, ks, drops, bar_sec) if from_drums else []
    if not from_drums:
        builds = detect_builds(y, sr, drops)

    print(f"  Detecting breakdowns...")
    breaks = breaks_from_structure(ks, 60.0 / bpm) if ks is not None else []
    if not breaks:
        breaks = detect_breakdowns(y, sr)
    builds = _drop_builds_on_breaks(builds, breaks, bar_sec)

    print(f"  Detecting intro...")
    intro = detect_intro(beat_times, grid.downbeat_times, _audio_start_sec(y, sr))

    print(f"  Detecting outros...")
    outros = detect_outros(duration, bpm)

    cues: List[CuePoint] = drops + builds + breaks + outros
    if intro:
        cues.append(intro)

    if stems is not None:
        print(f"  Detecting vocal entries...")
        cues.extend(detect_vocal_entries(audio_path, sr, stems))

    print(f"  Detecting section changes...")
    existing_times = [c.time_sec for c in cues]
    cues.extend(detect_sections(y, sr, existing_times))

    # Snap all cues to the beat grid
    cues = [_snap_cue(c, beat_times, grid.downbeat_times) for c in cues]

    cues = _dedupe_and_cap(cues)

    title, artist = _read_tags(audio_path)
    return TrackAnalysis(
        audio_path=str(Path(audio_path).resolve()),
        title=title,
        artist=artist,
        duration_sec=duration,
        bpm=round(bpm, 2),
        cues=cues,
        grid_source=grid.source,
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
        "grid_source": a.grid_source,
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
        grid_source=d.get("grid_source", "librosa"),
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


# Bump when detection changes enough that cached cues are stale.
ANALYSIS_VERSION = 4  # 2: drum-stem drops; 3: drum-structure breaks/builds, no intro breakdowns; 4: INTRO = track start


def grid_fingerprint(audio_path: str, rb_index: Optional[RekordboxIndex]) -> str:
    """Identity of the beat grid analysis would use: changes when Rekordbox
    re-analyses the track or the user edits its grid ("none" = own tracker)."""
    return rb_index.fingerprint(audio_path) if rb_index is not None else "none"


def _cache_key(audio_path: str, use_demucs: bool, grid_fp: str = "none") -> str:
    """Resolved path + mtime + demucs flag + grid identity, so a changed file,
    a switch between --no-lyrics and vocal-detection mode, or a different
    beat grid invalidates the entry instead of silently reusing cues
    computed under different settings."""
    resolved = str(Path(audio_path).resolve())
    mtime = Path(audio_path).stat().st_mtime
    return f"{resolved}::{mtime}::demucs={use_demucs}::grid={grid_fp}::v{ANALYSIS_VERSION}"


def analyze_track_cached(
    audio_path: str, cache: dict, *, use_demucs: bool = True,
    rb_index: Optional[RekordboxIndex] = None, stem_cache_dir: Optional[Path] = None,
) -> Tuple[TrackAnalysis, bool]:
    """
    Like analyze_track, but checks `cache` first and stores the result back
    into it on a miss (mutated in place — caller decides when to persist to
    disk, e.g. after every track so an interrupted run loses nothing).

    Returns (analysis, was_cached).
    """
    key = _cache_key(audio_path, use_demucs, grid_fingerprint(audio_path, rb_index))
    if key in cache:
        return _analysis_from_dict(cache[key]), True

    result = analyze_track(audio_path, use_demucs=use_demucs, rb_index=rb_index,
                           stem_cache_dir=stem_cache_dir)
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
