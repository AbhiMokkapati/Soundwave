"""
Synced lyrics lookup via LRCLIB (https://lrclib.net) — free, no API key.

LRCLIB matches by artist + title (+ optional album/duration) and returns
plain lyrics and/or an LRC-format synced lyric track. Results are cached to
disk so repeat lookups (e.g. reopening a track in the UI) don't re-hit the
network.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List, Optional, TypedDict

import requests

LRCLIB_BASE = "https://lrclib.net/api"
_USER_AGENT = "Soundwave/1.0.0 (soundwave-dj-tool)"

_LRC_LINE = re.compile(r"\[(\d{2}):(\d{2})(?:\.(\d{1,3}))?\](.*)")


class LyricLine(TypedDict):
    time_sec: float
    text: str


class LyricsResult(TypedDict):
    synced: bool
    lines: List[LyricLine]     # empty when no lyrics found at all
    plain: Optional[str]       # raw plain text, when available


def _parse_lrc(lrc_text: str) -> List[LyricLine]:
    lines: List[LyricLine] = []
    for raw_line in lrc_text.splitlines():
        m = _LRC_LINE.match(raw_line.strip())
        if not m:
            continue
        minutes, seconds, frac, text = m.groups()
        frac_val = float(f"0.{frac}") if frac else 0.0
        t = int(minutes) * 60 + int(seconds) + frac_val
        text = text.strip()
        if text:
            lines.append({"time_sec": t, "text": text})
    lines.sort(key=lambda l: l["time_sec"])
    return lines


def _cache_key(artist: str, title: str, duration_sec: Optional[float]) -> str:
    dur = int(duration_sec) if duration_sec else 0
    safe = re.sub(r"[^\w\-]+", "_", f"{artist}__{title}__{dur}".lower())
    return safe[:200]


def fetch_lyrics(
    artist: str,
    title: str,
    duration_sec: Optional[float] = None,
    *,
    cache_dir: Optional[Path] = None,
) -> LyricsResult:
    """
    Look up lyrics for a track. Tries an exact /get match first (needs a
    close duration match), then falls back to /search (fuzzy, no duration
    requirement) and takes the top result.

    Returns {"synced": False, "lines": [], "plain": None} when nothing is
    found — never raises for a missing match, only for network-level errors
    being swallowed internally (best-effort lookup).
    """
    cache_path = None
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / f"{_cache_key(artist, title, duration_sec)}.json"
        if cache_path.exists():
            try:
                return json.loads(cache_path.read_text(encoding="utf-8"))
            except Exception:
                pass

    result: LyricsResult = {"synced": False, "lines": [], "plain": None}
    headers = {"User-Agent": _USER_AGENT}

    data = None
    if duration_sec:
        try:
            r = requests.get(
                f"{LRCLIB_BASE}/get",
                params={"artist_name": artist, "track_name": title,
                        "duration": int(duration_sec)},
                headers=headers, timeout=10,
            )
            if r.status_code == 200:
                data = r.json()
        except Exception:
            data = None

    if data is None:
        try:
            r = requests.get(
                f"{LRCLIB_BASE}/search",
                params={"artist_name": artist, "track_name": title},
                headers=headers, timeout=10,
            )
            if r.status_code == 200:
                results = r.json()
                if results:
                    data = results[0]
        except Exception:
            data = None

    if data:
        synced_lrc = data.get("syncedLyrics")
        plain = data.get("plainLyrics")
        if synced_lrc:
            result["synced"] = True
            result["lines"] = _parse_lrc(synced_lrc)
        result["plain"] = plain or None
        if not result["lines"] and plain:
            result["lines"] = [
                {"time_sec": 0.0, "text": line}
                for line in plain.splitlines() if line.strip()
            ]

    if cache_path is not None:
        cache_path.write_text(json.dumps(result), encoding="utf-8")

    return result
