"""
Audio similarity: extract per-track feature vectors and use them to build
Rekordbox playlists — either grouped by sound (clusters) or ordered into a
single smooth-flowing set (nearest-neighbor path).

Features extracted per track (all from a short slice around the middle of
the file, not the full track, to keep this fast on large libraries):
  - tempo (BPM)
  - chroma vector (12-dim) — captures key/harmonic content
  - spectral centroid, bandwidth, rolloff, zero-crossing rate — timbre/brightness
  - RMS energy — loudness/intensity
  - MFCCs (13) — overall timbral fingerprint

Results are cached to a JSON sidecar file keyed by resolved path + mtime, so
re-running clustering/path-building after the first pass is nearly instant.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

# Order defines the feature vector layout; kept explicit so the cache format
# is stable and debuggable.
_FEATURE_KEYS = [
    "tempo", "centroid", "bandwidth", "rolloff", "zcr", "rms",
] + [f"chroma_{i}" for i in range(12)] + [f"mfcc_{i}" for i in range(13)]


def _extract_raw_features(audio_path: str, analysis_sec: float) -> Optional[Dict[str, float]]:
    import librosa

    try:
        duration = librosa.get_duration(path=audio_path)
        offset = max(0.0, (duration - analysis_sec) / 2)
        y, sr = librosa.load(audio_path, sr=22050, mono=True,
                              offset=offset, duration=analysis_sec)
        if y.size == 0:
            return None

        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr).mean(axis=1)
        centroid = librosa.feature.spectral_centroid(y=y, sr=sr).mean()
        bandwidth = librosa.feature.spectral_bandwidth(y=y, sr=sr).mean()
        rolloff = librosa.feature.spectral_rolloff(y=y, sr=sr).mean()
        zcr = librosa.feature.zero_crossing_rate(y=y).mean()
        rms = librosa.feature.rms(y=y).mean()
        mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13).mean(axis=1)

        values = [float(np.atleast_1d(tempo)[0]), float(centroid), float(bandwidth),
                  float(rolloff), float(zcr), float(rms)]
        values += [float(c) for c in chroma]
        values += [float(m) for m in mfcc]
        return dict(zip(_FEATURE_KEYS, values))
    except Exception:
        return None


def _load_cache(cache_path: Path) -> dict:
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_cache(cache_path: Path, cache: dict) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def extract_features(
    paths: List[str],
    cache_path: Path,
    *,
    analysis_sec: float = 60.0,
    recompute: bool = False,
    progress=None,
    on_progress=None,
) -> Dict[str, Dict[str, float]]:
    """
    Return {path: {feature_name: value}} for every path, using and updating
    a JSON cache keyed by (resolved path, mtime) so unchanged files are only
    analyzed once.

    on_progress(index, total, path, cached), if given, is called once per
    file as it's processed (from cache or freshly analyzed) — for callers
    that want to report progress somewhere other than a terminal (e.g. SSE).
    """
    cache = {} if recompute else _load_cache(cache_path)
    results: Dict[str, Dict[str, float]] = {}
    dirty = False
    total = len(paths)

    iterator = tqdm_wrap(paths, progress)
    for i, path in enumerate(iterator, 1):
        resolved = str(Path(path).resolve())
        try:
            mtime = Path(path).stat().st_mtime
        except OSError:
            continue
        key = f"{resolved}::{mtime}"

        if key in cache:
            results[resolved] = cache[key]
            if on_progress:
                on_progress(i, total, path, True)
            continue

        feats = _extract_raw_features(path, analysis_sec)
        if feats is None:
            if on_progress:
                on_progress(i, total, path, False)
            continue
        cache[key] = feats
        results[resolved] = feats
        dirty = True
        if on_progress:
            on_progress(i, total, path, False)

    if dirty:
        _save_cache(cache_path, cache)

    return results


def tqdm_wrap(paths, progress):
    if progress is None:
        return paths
    return progress(paths)


def _feature_matrix(features_by_path: Dict[str, Dict[str, float]]):
    """Return (paths, raw_matrix, normalized_matrix)."""
    paths = list(features_by_path.keys())
    matrix = np.array([[features_by_path[p][k] for k in _FEATURE_KEYS] for p in paths])
    mean = matrix.mean(axis=0)
    std = matrix.std(axis=0)
    std[std == 0] = 1.0
    normalized = (matrix - mean) / std
    return paths, matrix, normalized


def cluster_tracks(
    features_by_path: Dict[str, Dict[str, float]],
    n_clusters: int,
) -> Dict[int, List[str]]:
    """Group tracks into n_clusters similarity clusters via KMeans. Returns
    {cluster_index: [paths]}, ordered by descending cluster size."""
    from sklearn.cluster import KMeans

    paths, matrix, normalized = _feature_matrix(features_by_path)
    n_clusters = max(1, min(n_clusters, len(paths)))

    if len(paths) <= 1 or n_clusters <= 1:
        return {0: paths}

    labels = KMeans(n_clusters=n_clusters, n_init=10, random_state=0).fit_predict(normalized)

    groups: Dict[int, List[str]] = {}
    for path, label in zip(paths, labels):
        groups.setdefault(int(label), []).append(path)

    # Renumber by descending size so "Cluster 1" is always the biggest group.
    ordered = sorted(groups.values(), key=len, reverse=True)
    return {i: g for i, g in enumerate(ordered)}


def cluster_summary(
    features_by_path: Dict[str, Dict[str, float]],
    paths_in_cluster: List[str],
) -> str:
    """Human-readable descriptor for a cluster: avg BPM + energy level."""
    if not paths_in_cluster:
        return "empty"
    tempos = [features_by_path[p]["tempo"] for p in paths_in_cluster]
    energies = [features_by_path[p]["rms"] for p in paths_in_cluster]
    avg_tempo = sum(tempos) / len(tempos)
    avg_energy = sum(energies) / len(energies)
    all_energies = [f["rms"] for f in features_by_path.values()]
    lo, hi = min(all_energies), max(all_energies)
    span = hi - lo or 1.0
    pct = (avg_energy - lo) / span
    level = "low-energy" if pct < 0.33 else "high-energy" if pct > 0.66 else "mid-energy"
    return f"{avg_tempo:.0f} BPM, {level}"


def similarity_path_order(features_by_path: Dict[str, Dict[str, float]]) -> List[str]:
    """
    Greedy nearest-neighbor ordering: start from the lowest-energy track and
    repeatedly jump to the closest not-yet-visited track in normalized
    feature space. Not globally optimal (that's NP-hard), but gives a smooth
    track-to-track flow suitable for a DJ set order.
    """
    paths, matrix, normalized = _feature_matrix(features_by_path)
    n = len(paths)
    if n <= 2:
        return paths

    start = int(np.argmin(normalized[:, _FEATURE_KEYS.index("rms")]))
    visited = [False] * n
    order = [start]
    visited[start] = True

    current = start
    for _ in range(n - 1):
        dists = np.linalg.norm(normalized - normalized[current], axis=1)
        dists[visited] = np.inf
        nxt = int(np.argmin(dists))
        order.append(nxt)
        visited[nxt] = True
        current = nxt

    return [paths[i] for i in order]
