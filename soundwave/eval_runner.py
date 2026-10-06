"""
Runs the cue-placement evaluation over a set of tracks and formats a report.
Metric definitions live in soundwave.evaluate; this is the orchestration.

For each track the pipeline runs in up to three modes, all scored against
Rekordbox's analysis of the same file, so a change shows up as a before/after
rather than an absolute number you have to interpret:

  legacy     own librosa beat grid, loudness-based drops
  rb-grid    snapped to Rekordbox's grid, loudness-based drops
  rb+drums   Rekordbox grid + drop detection from the Demucs drum stem
             (only when stems are enabled - Demucs runs at ~1x realtime, so
             pass a stem cache directory to make re-runs cheap)
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import random
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

import librosa

from soundwave.analyzer import _get_beat_times, analyze_track, separate_stems
from soundwave.evaluate import (
    PHRASE_PROXY, cue_phrase_alignment, cue_reference_metrics, grid_agreement,
    summarize_reference,
    truth_metrics, truth_template,
)
from soundwave.rekordbox.anlz_grid import RekordboxIndex, _norm


def sample_tracks(index: RekordboxIndex, root: Optional[str], n: int, seed: int) -> List[str]:
    paths = [p for p in index.audio_paths() if os.path.exists(p)]
    if root:
        r = _norm(root)
        paths = [p for p in paths if _norm(p).startswith(r)]
    random.Random(seed).shuffle(paths)
    return sorted(paths[:n])


def _quiet_analyze(path: str, rb_index: Optional[RekordboxIndex], stems=None):
    with contextlib.redirect_stdout(io.StringIO()):
        return analyze_track(path, use_demucs=stems is not None, rb_index=rb_index, stems=stems)


def evaluate_tracks(
    paths: List[str], index: RekordboxIndex, use_stems: bool = False,
    stem_cache_dir: Optional[Path] = None,
    truth: Optional[Dict[str, List[dict]]] = None, progress=print,
) -> dict:
    modes = ["legacy", "rb-grid"] + (["rb+drums"] if use_stems else [])
    refs = {m: [] for m in modes}
    alignment = {m: {lab: defaultdict(int) for lab in PHRASE_PROXY} for m in modes}
    agreements = []
    truth_rows = {m: [] for m in modes}
    templates: Dict[str, List[dict]] = {}
    skipped = []
    truth_by_norm = {_norm(k): v for k, v in (truth or {}).items()}

    for i, path in enumerate(paths, 1):
        grid = index.grid_for(path)
        if grid is None:
            skipped.append(path)
            continue
        progress(f"[{i}/{len(paths)}] {Path(path).name}")
        try:
            y, sr = librosa.load(path, sr=None, mono=True)
            _, beats = _get_beat_times(y, sr)
            results = {"legacy": _quiet_analyze(path, None), "rb-grid": _quiet_analyze(path, index)}
            if use_stems:
                stems = separate_stems(path, sr, stem_cache_dir)
                if stems is None:
                    raise RuntimeError("stem separation failed")
                results["rb+drums"] = _quiet_analyze(path, index, stems)
        except Exception as exc:
            progress(f"    ERROR: {exc}")
            skipped.append(path)
            continue

        t = truth_by_norm.get(_norm(path))
        for m in modes:
            refs[m] += cue_reference_metrics(results[m].cues, grid)
            for lab in PHRASE_PROXY:
                for k, v in cue_phrase_alignment(results[m].cues, grid, lab).items():
                    alignment[m][lab][k] += v
            if t is not None:
                truth_rows[m].append(truth_metrics(results[m].cues, t, grid.bpm))
        ga = grid_agreement(beats, grid)
        if ga:
            agreements.append(ga)
        templates[path] = truth_template(results[modes[-1]].cues)

    return {
        "n_tracks": len(paths) - len(skipped),
        "skipped": skipped,
        "modes": modes,
        "reference": {k: summarize_reference(v) for k, v in refs.items()},
        "alignment": {m: {lab: dict(v) for lab, v in d.items()} for m, d in alignment.items()},
        "grid": {
            "n": len(agreements),
            "median_offset_ms": round(statistics.median(a.median_offset_ms for a in agreements), 1) if agreements else None,
            "bar_phase_ok_median_pct": round(statistics.median(a.bar_phase_ok_pct for a in agreements), 1) if agreements else None,
            "tracks_bar_phase_wrong": sum(a.bar_phase_ok_pct < 50 for a in agreements),
        },
        "truth": {k: _roll_up_truth(v) for k, v in truth_rows.items() if v},
        "template": templates,
    }


def _roll_up_truth(rows: List[dict]) -> dict:
    """Pool per-track truth results, weighting by number of truth cues."""
    total = sum(r["n_truth"] for r in rows)
    if not total:
        return {}
    out = {"n_tracks": len(rows), "n_truth": total,
           "missed": sum(r["missed"] for r in rows),
           "false_positives": sum(r["false_positives"] for r in rows)}
    for key in rows[0]:
        if key.endswith("_pct"):
            out[key] = round(sum(r[key] * r["n_truth"] for r in rows if r[key] is not None) / total, 1)
    errs = [r["median_abs_err_sec"] for r in rows if r["median_abs_err_sec"] is not None]
    out["median_of_track_median_err_sec"] = round(statistics.median(errs), 3) if errs else None
    return out


def format_report(result: dict) -> str:
    L: List[str] = []
    g = result["grid"]
    L.append(f"Tracks evaluated: {result['n_tracks']}  (skipped: {len(result['skipped'])})")
    L.append("")
    L.append("1. Soundwave's own beat tracker vs Rekordbox's grid")
    if g["n"]:
        L.append(f"   median offset: {g['median_offset_ms']:+.0f} ms (positive = Soundwave beats late)")
        L.append(f"   bar phase right: {g['bar_phase_ok_median_pct']:.0f}% of 'downbeats' (median track); "
                 f"{g['tracks_bar_phase_wrong']}/{g['n']} tracks have the wrong bar phase")
    L.append("")
    L.append("2. Cues vs Rekordbox's grid and phrases")
    hdr = f"   {'':8}{'cues':>5} | {'on-beat%':>9} {'on-bar%':>8} {'med bar off ms':>15} | {'on-phrase%':>11} {'<=2 bars%':>10}"
    for mode in result["modes"]:
        L.append(f"   [{mode}]")
        L.append(hdr)
        summ = result["reference"][mode]
        for label in ["ALL"] + sorted(k for k in summ if k != "ALL"):
            s = summ[label]
            f = lambda v: "-" if v is None else f"{v}"
            L.append(f"   {label:8}{s['n']:>5} | {f(s['on_beat_pct']):>9} {f(s['on_downbeat_pct']):>8} "
                     f"{s['median_downbeat_off_ms']:>15} | {f(s['on_phrase_pct']):>11} {f(s['near_phrase_pct']):>10}")
    L.append("")
    L.append("   Cues vs matching Rekordbox phrase starts (within 1 bar; tracks with a high-mood phrase set only)")
    L.append("   recall = phrases with a cue nearby; precision = cues sitting on such a phrase")
    for mode in result["modes"]:
        parts = []
        for lab, (proxy, _) in PHRASE_PROXY.items():
            c = result["alignment"][mode][lab]
            if not c.get("n_phrase"):
                continue
            prec = f"{100 * c['cue_on_phrase'] / c['n_cues']:.0f}%" if c["n_cues"] else "-"
            parts.append(f"{lab}~{proxy}: recall {c['phrase_hit']}/{c['n_phrase']} "
                         f"({100 * c['phrase_hit'] / c['n_phrase']:.0f}%), precision "
                         f"{c['cue_on_phrase']}/{c['n_cues']} ({prec})")
        L.append(f"   [{mode}] " + "; ".join(parts))
    if result["truth"]:
        L.append("")
        L.append("3. Accuracy vs hand-corrected truth")
        for mode, t in result["truth"].items():
            L.append(f"   [{mode}] " + ", ".join(f"{k}={v}" for k, v in t.items()))
    else:
        L.append("")
        L.append("3. Accuracy vs hand-corrected truth: not run (no --truth file). Sections 1-2 only")
        L.append("   measure grid/phrase alignment — they cannot say the right section was found.")
    return "\n".join(L)


def load_truth(path: str) -> Dict[str, List[dict]]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
