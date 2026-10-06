"""
Read Rekordbox's own beat grid and phrase analysis for tracks already in the
user's library.

Why: a hot cue is judged against the grid *Rekordbox displays* (and that the
user may have hand-corrected), not against whatever grid we computed
ourselves. librosa's beat tracker has no downbeat concept and runs ~20-60 ms
late, so cues snapped to it drift off Rekordbox's grid and bar boundaries
are a coin-flip. Rekordbox's ANLZ files carry the real beat numbers (1-4 in
bar) and, in the .EXT file, its phrase (PSSI) boundaries.

Everything here is read-only. master.db is copied to a temp dir before
opening so a running Rekordbox's file is never touched.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

MIN_GRID_BEATS = 16  # shorter than this is not a usable grid


@dataclass(frozen=True)
class Phrase:
    time_sec: float
    kind: int   # raw PSSI kind; its meaning depends on `mood`
    mood: int   # 1 = high, 2 = mid, 3 = low


@dataclass
class RekordboxGrid:
    beat_times: np.ndarray      # seconds, ascending
    beat_numbers: np.ndarray    # position in bar, 1..4
    bpms: np.ndarray            # per-beat BPM (varies on dynamic grids)
    phrases: List[Phrase] = field(default_factory=list)
    fingerprint: str = ""

    @property
    def downbeat_times(self) -> np.ndarray:
        return self.beat_times[self.beat_numbers == 1]

    @property
    def bpm(self) -> float:
        return float(np.median(self.bpms))


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def default_rekordbox_dirs() -> List[Path]:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", "")) / "Pioneer"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Pioneer"
    else:
        return []
    return [base / "rekordbox", base / "rekordbox6"]


def _parse_grid(dat_path: Path) -> Optional[RekordboxGrid]:
    from pyrekordbox.anlz import AnlzFile

    dat = AnlzFile.parse_file(str(dat_path))
    raw = dat.get("beat_grid")
    if raw is None:
        return None
    numbers, bpms, times = (np.asarray(x) for x in raw)
    if len(times) < MIN_GRID_BEATS:
        return None
    if np.any(np.diff(times) <= 0) or numbers.min() < 1 or numbers.max() > 4:
        return None  # malformed grid — fall back rather than snap to garbage

    phrases: List[Phrase] = []
    ext_path = dat_path.with_suffix(".EXT")
    if ext_path.exists():
        try:
            pssi = AnlzFile.parse_file(str(ext_path)).get("PSSI")
            for entry in (pssi.entries if pssi is not None else []):
                idx = int(entry.beat) - 1  # PSSI beat numbers are 1-based indices into the grid
                if 0 <= idx < len(times):
                    phrases.append(Phrase(float(times[idx]), int(entry.kind), int(pssi.mood)))
        except Exception:
            phrases = []  # phrases are optional; the grid alone is still useful

    st = dat_path.stat()
    return RekordboxGrid(
        beat_times=times.astype(float),
        beat_numbers=numbers.astype(int),
        bpms=bpms.astype(float),
        phrases=phrases,
        fingerprint=f"rb:{st.st_mtime_ns}",
    )


class RekordboxIndex:
    """audio file path -> Rekordbox ANLZ .DAT file, built from master.db."""

    def __init__(self, entries: Dict[str, Path], originals: Optional[Dict[str, str]] = None):
        self._entries = entries
        self._originals = originals or {}  # normalised key -> path as Rekordbox stores it

    def __len__(self) -> int:
        return len(self._entries)

    @classmethod
    def load(cls, rekordbox_dir: Optional[str] = None) -> Optional["RekordboxIndex"]:
        """Return an index, or None if Rekordbox data isn't available (not
        installed, DB unreadable, pyrekordbox missing) — callers fall back
        to their own beat tracking."""
        try:
            from pyrekordbox import Rekordbox6Database
        except Exception:
            return None

        dirs = [Path(rekordbox_dir)] if rekordbox_dir else default_rekordbox_dirs()
        for rb_dir in dirs:
            db_file = rb_dir / "master.db"
            if not db_file.exists():
                continue
            tmp = tempfile.mkdtemp(prefix="soundwave_rb_")
            try:
                shutil.copy(db_file, Path(tmp) / "master.db")
                db = Rekordbox6Database(path=str(Path(tmp) / "master.db"))
                entries: Dict[str, Path] = {}
                originals: Dict[str, str] = {}
                for c in db.get_content().all():
                    if not c.FolderPath or not c.AnalysisDataPath:
                        continue
                    dat = rb_dir / "share" / c.AnalysisDataPath.lstrip("/\\")
                    if dat.exists():
                        entries[_norm(c.FolderPath)] = dat
                        originals[_norm(c.FolderPath)] = c.FolderPath
                db.close()
                if entries:
                    return cls(entries, originals)
            except Exception as exc:
                print(f"  [warn] Could not read Rekordbox library ({exc}); using own beat tracking.")
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        return None

    def audio_paths(self) -> List[str]:
        """Audio file paths of every analysed track, as Rekordbox stores them."""
        return sorted(self._originals.values())

    def _dat_for(self, audio_path: str) -> Optional[Path]:
        for p in (audio_path, str(Path(audio_path).resolve())):
            dat = self._entries.get(_norm(p))
            if dat is not None:
                return dat
        return None

    def fingerprint(self, audio_path: str) -> str:
        """Cheap (stat-only) identity of the grid that would be used, so
        cached analyses are invalidated when Rekordbox re-analyses a track
        or the user edits its grid."""
        dat = self._dat_for(audio_path)
        if dat is None:
            return "none"
        try:
            return f"rb:{dat.stat().st_mtime_ns}"
        except OSError:
            return "none"

    def grid_for(self, audio_path: str) -> Optional[RekordboxGrid]:
        dat = self._dat_for(audio_path)
        if dat is None:
            return None
        try:
            return _parse_grid(dat)
        except Exception:
            return None
