"""
Library management: fill missing tags, sort by genre, deduplicate, rename.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".aiff", ".aif", ".m4a", ".ogg"}

# Characters that are illegal in Windows *and* POSIX filenames
_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _collect_audio(root: Path, ignore: set[str] | None = None) -> List[Path]:
    """Collect all audio files under root, skipping any folder whose name
    (case-insensitive) appears in the ignore set."""
    ignored = {n.lower() for n in (ignore or [])}

    def _keep(f: Path) -> bool:
        if not (f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS):
            return False
        try:
            rel_parts = f.relative_to(root).parts
        except ValueError:
            return True
        return not any(part.lower() in ignored for part in rel_parts[:-1])

    return sorted(f for f in root.rglob("*") if _keep(f))


def _sanitize(name: str) -> str:
    """Strip characters that are invalid in file-system names."""
    name = _ILLEGAL_CHARS.sub("_", name).strip(". ")
    return name or "Unknown"


def _read_easy_tags(path: Path):
    """
    Return a mutagen easy-tags object ready for string-based get/set, or None.

    Handles three edge cases:
      - File has no tag block yet  -> add_tags() to create one
      - WAV / formats where easy=True returns raw ID3  -> skip (return None)
      - Any other mutagen error    -> return None
    """
    try:
        from mutagen import File as MutagenFile
        f = MutagenFile(str(path), easy=True)
        if f is None:
            return None
        if f.tags is None:
            try:
                f.add_tags()
            except Exception:
                return None
        # Reject raw ID3 objects — they require Frame instances, not plain strings
        from mutagen.id3 import ID3
        from mutagen.easyid3 import EasyID3
        if isinstance(f.tags, ID3) and not isinstance(f.tags, EasyID3):
            return None
        return f
    except Exception:
        return None


def _parse_artist_title_from_filename(stem: str) -> Tuple[Optional[str], Optional[str]]:
    for sep in (" - ", " – ", " — ", "_-_"):
        if sep in stem:
            parts = stem.split(sep, 1)
            return parts[0].strip(), parts[1].strip()
    return None, None


# Matches leading track numbers: "01 ", "01. ", "01 - ", "1. ", "(01) "
_LEADING_TRACKNUM = re.compile(r"^[\(\[]?\d{1,3}[\)\]]?[.\s\-]+")

# YouTube / download metadata noise to strip from the END of a title
# Order matters — strip outermost first; applied repeatedly until stable
_YOUTUBE_NOISE = re.compile(
    r"""[\[\(]                          # opening bracket
    \s*
    (?:official\s+)?                    # optional "Official "
    (?:
        audio | video | music\s+video |
        lyric\s+video | lyrics? |
        visuali[sz]er | hd | hq |
        full\s+(?:audio|video) |
        audio\s+official | official\s+audio |
        official\s+music\s+video |
        official\s+lyric\s+video
    )
    \s*
    [\]\)]                              # closing bracket""",
    re.IGNORECASE | re.VERBOSE,
)

# Trailing quality/version markers like " 4", " (1)" at end of filename
_TRAILING_NUMBER = re.compile(r"\s+\d+$")


def _strip_youtube_noise(title: str, *, strip_trailing_number: bool = True) -> str:
    """Remove YouTube metadata noise from anywhere in a title string.

    strip_trailing_number should be False for titles that came from a real
    tag rather than a downloaded filename: "Studio 54" and "Blink 182" are
    complete titles, not a filename with a stray counter appended.
    """
    prev = None
    while prev != title:
        prev = title
        title = _YOUTUBE_NOISE.sub("", title).strip()
    if strip_trailing_number:
        title = _TRAILING_NUMBER.sub("", title).strip()
    # Collapse any double spaces left behind
    title = re.sub(r"  +", " ", title).strip(" -")
    return title


def _clean_title_from_stem(stem: str) -> Optional[str]:
    if re.fullmatch(r"\d+", stem.strip()):
        return None
    # Strip leading track numbers: "01 - Lose Yourself" -> "Lose Yourself"
    cleaned = _LEADING_TRACKNUM.sub("", stem).strip()
    if not cleaned or re.fullmatch(r"[\d\W_]+", cleaned):
        return None
    # Strip YouTube noise: "(Official Audio)", "[Lyrics]", etc.
    cleaned = _strip_youtube_noise(cleaned)
    return cleaned or None


# ---------------------------------------------------------------------------
# 1. Fill missing tags
# ---------------------------------------------------------------------------

def fill_missing_tags(
    root: Path,
    *,
    fill_bpm: bool = False,
    ignore: set[str] | None = None,
    dry_run: bool = False,
) -> Iterator[str]:
    for path in _collect_audio(root, ignore=ignore):
        tags = _read_easy_tags(path)
        if tags is None:
            yield f"SKIP (unreadable tags): {path.name}"
            continue

        dirty = False
        stem = path.stem
        parsed_artist, parsed_title = _parse_artist_title_from_filename(stem)

        # --- title ---
        existing_title = (tags.get("title") or [None])[0]
        existing_title_str = (existing_title or "").strip()
        title_is_bad = (
            not existing_title_str
            or re.fullmatch(r"\d+", existing_title_str)
        )
        if title_is_bad:
            raw = parsed_title or _clean_title_from_stem(stem)
            new_title = _strip_youtube_noise(raw) if raw else None
            if new_title:
                if not dry_run:
                    tags["title"] = new_title
                yield f"title: '{path.name}' -> '{new_title}'"
                dirty = True
            else:
                yield f"title SKIP (no clean title found): '{path.name}'"

        # --- artist ---
        existing_artist = (tags.get("artist") or [None])[0]
        if (not existing_artist or existing_artist.strip() in ("", "Unknown")) and parsed_artist:
            if not dry_run:
                tags["artist"] = parsed_artist
            yield f"artist: '{path.name}' -> '{parsed_artist}'"
            dirty = True

        # --- genre (only from a *genre subfolder*, not the top-level collection) ---
        # Depth rule: root/collection/genre/file.mp3 → parent.parent != root
        # Avoids labelling files in flat collections like "touse" with genre "Touse"
        existing_genre = (tags.get("genre") or [None])[0]
        if not existing_genre or not existing_genre.strip():
            parent = path.parent
            if parent != root and parent.parent != root and parent.name:
                new_genre = parent.name.replace("_", " ").title()
                if not dry_run:
                    tags["genre"] = new_genre
                yield f"genre: '{path.name}' -> '{new_genre}'"
                dirty = True

        # --- BPM (slow: loads audio) ---
        if fill_bpm:
            existing_bpm = (tags.get("bpm") or [None])[0]
            if not existing_bpm or not existing_bpm.strip():
                try:
                    import librosa
                    import numpy as np
                    y, sr = librosa.load(str(path), sr=None, mono=True)
                    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
                    bpm_val = str(round(float(np.atleast_1d(tempo)[0]), 1))
                    if not dry_run:
                        tags["bpm"] = bpm_val
                    yield f"bpm: '{path.name}' -> {bpm_val}"
                    dirty = True
                except Exception as exc:
                    yield f"bpm ERROR '{path.name}': {exc}"

        if dirty and not dry_run:
            try:
                tags.save()
            except Exception as exc:
                yield f"SAVE ERROR '{path.name}': {exc}"


# ---------------------------------------------------------------------------
# 1b. Clear bogus genre tags written by a bad --fill-tags run
# ---------------------------------------------------------------------------

def clear_genre_from_folders(
    root: Path,
    folders: set[str],
    *,
    ignore: set[str] | None = None,
    dry_run: bool = False,
) -> Iterator[str]:
    """
    Clear the genre tag on files sitting directly inside any of the named
    folders, but only when the tag exactly matches (case/separator-insensitive)
    that folder's own name.

    Fixes corruption from an earlier --fill-tags run: its genre heuristic
    writes the immediate parent folder's name into the genre tag for any file
    2+ levels deep, with no check that the folder is actually a genre (e.g.
    personal buckets like "Partyyyy" or "Pool Party" get "genre" = their own
    name). That self-referential tag then makes --sort-genres treat the file
    as already correctly filed, so it never moves.

    Only clears folders you explicitly name — real genre folders (House,
    Trap, Pop, ...) are left untouched even though their files also have
    genre == folder name, since that happens to be correct for them.
    """
    folders_norm = {_norm_genre(f) for f in folders}
    for path in _collect_audio(root, ignore=ignore):
        if _norm_genre(path.parent.name) not in folders_norm:
            continue

        tags = _read_easy_tags(path)
        if tags is None:
            continue

        existing_genre = (tags.get("genre") or [None])[0]
        if not existing_genre:
            continue
        if _norm_genre(existing_genre) != _norm_genre(path.parent.name):
            continue

        yield f"CLEAR genre '{existing_genre}': '{path.relative_to(root)}'"
        if not dry_run:
            del tags["genre"]
            try:
                tags.save()
            except Exception as exc:
                yield f"SAVE ERROR '{path.name}': {exc}"


# ---------------------------------------------------------------------------
# 2. Sort into genre sub-folders
# ---------------------------------------------------------------------------

def _norm_genre(s: str) -> str:
    """Normalise a genre string for folder matching: lowercase, collapse separators."""
    return re.sub(r"[\-_/\\&\s]+", " ", s).strip().lower()


def sort_by_genre(
    root: Path,
    *,
    ignore: set[str] | None = None,
    dry_run: bool = False,
) -> Iterator[str]:
    """
    Move each audio file into the appropriate genre sub-folder.

    - Structured collections (already have genre sub-folders, e.g. DJ Tracks):
      files are moved within the same collection; new genre folders are created
      as needed.
    - Flat collections (no genre sub-folders, e.g. touse, indian party):
      files are routed to the PRIMARY collection (the one with the most existing
      genre sub-folders), which is auto-detected.  New genre folders are created
      there as needed.

    Genre matching is case-insensitive and normalises hyphens/underscores/slashes.
    """
    ignored_lower = {n.lower() for n in (ignore or [])}

    # Build collection -> {norm_genre: folder_path} map for every root-level dir
    collections: Dict[Path, Dict[str, Path]] = {}
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name.lower() in ignored_lower:
            continue
        collections[d] = {
            _norm_genre(sd.name): sd
            for sd in d.iterdir()
            if sd.is_dir() and sd.name.lower() not in ignored_lower
        }

    # Primary collection = the one with the most genre sub-folders
    primary: Optional[Path] = None
    if collections:
        primary = max(collections, key=lambda c: len(collections[c]))

    if primary:
        yield f"Primary collection (genre target for flat folders): '{primary.name}'"

    for path in _collect_audio(root, ignore=ignore):
        try:
            rel_parts = path.relative_to(root).parts
        except ValueError:
            continue

        collection = root / rel_parts[0]
        coll_folders = collections.get(collection, {})
        is_flat = len(coll_folders) == 0

        tags = _read_easy_tags(path)
        genre_raw = None
        if tags is not None:
            genre_raw = (tags.get("genre") or [None])[0]

        genre_name = (genre_raw or "").strip()
        if not genre_name:
            continue  # no genre tag, silently skip

        # Route flat-collection files to the primary collection
        if is_flat and primary and primary != collection:
            target_collection = primary
            target_folders = collections[primary]
        else:
            target_collection = collection
            target_folders = coll_folders

        norm = _norm_genre(genre_name)
        genre_dir = target_folders.get(norm)

        # Create the genre folder if it doesn't exist yet
        if genre_dir is None:
            folder_name = _sanitize(genre_name)
            genre_dir = target_collection / folder_name
            yield f"CREATE '{genre_dir.relative_to(root)}'"
            if not dry_run:
                genre_dir.mkdir(exist_ok=True)
            # Update the live cache so later files reuse it
            collections[target_collection][norm] = genre_dir

        dest = genre_dir / path.name
        if dest == path:
            continue  # already in the right place

        if dest.exists():
            stem, suffix = path.stem, path.suffix
            counter = 2
            while dest.exists():
                dest = genre_dir / f"{stem} ({counter}){suffix}"
                counter += 1

        yield f"MOVE '{path.relative_to(root)}' -> '{dest.relative_to(root)}'"

        if not dry_run:
            shutil.move(str(path), str(dest))


# ---------------------------------------------------------------------------
# 3. Deduplicate by title
# ---------------------------------------------------------------------------

def dedupe_by_title(
    root: Path,
    *,
    ignore: set[str] | None = None,
    dry_run: bool = False,
) -> Iterator[str]:
    """
    Scan all files first to find duplicates, then yield a line per deletion.
    Keeps the largest file in each duplicate group.

    Duplicates share both title and artist: title alone would delete
    different songs that happen to share a name ("Closer", "Home", "Intro").
    """
    yield "Scanning for duplicates..."

    groups: Dict[str, List[Path]] = {}
    for path in _collect_audio(root, ignore=ignore):
        tags = _read_easy_tags(path)
        title_raw = None
        if tags is not None:
            title_raw = (tags.get("title") or [None])[0]
        artist_raw = (tags.get("artist") or [None])[0] if tags is not None else None
        title_key = (
            (artist_raw or "").strip().lower(),
            (title_raw or path.stem).strip().lower(),
        )
        groups.setdefault(title_key, []).append(path)

    found_any = False
    for title_key, paths in groups.items():
        if len(paths) < 2:
            continue
        found_any = True
        paths_sorted = sorted(paths, key=lambda p: p.stat().st_size, reverse=True)
        keeper = paths_sorted[0]
        for dupe in paths_sorted[1:]:
            yield f"DELETE (dup of '{keeper.name}'): '{dupe.relative_to(root)}'"
            if not dry_run:
                dupe.unlink()

    if not found_any:
        yield "No duplicates found."


# ---------------------------------------------------------------------------
# 4. Rename files to their track title
# ---------------------------------------------------------------------------

def rename_to_track_name(
    root: Path,
    *,
    ignore: set[str] | None = None,
    dry_run: bool = False,
) -> Iterator[str]:
    for path in _collect_audio(root, ignore=ignore):
        tags = _read_easy_tags(path)
        title_raw = None
        if tags is not None:
            title_raw = (tags.get("title") or [None])[0]

        title_str = (title_raw or "").strip()

        if re.fullmatch(r"\d+", title_str):
            yield f"SKIP (title is bare number '{title_str}'): '{path.name}'"
            continue

        if title_str:
            # The tag is already a title, so don't re-parse it as "Artist -
            # Title" ("Levitating - Remix" would become "Remix") or strip a
            # trailing number ("Studio 54" would become "Studio"). Only the
            # bracketed download noise ("Havana (Audio)") is safe to drop.
            title_str = _strip_youtube_noise(title_str, strip_trailing_number=False)

        if not title_str:
            title_str = _clean_title_from_stem(path.stem) or ""
        if not title_str:
            yield f"SKIP (no title tag): '{path.name}'"
            continue

        new_stem = _sanitize(title_str)
        new_name = new_stem + path.suffix.lower()
        dest = path.parent / new_name

        if dest == path:
            continue  # name unchanged

        if dest.exists():
            counter = 2
            while dest.exists():
                dest = path.parent / f"{new_stem} ({counter}){path.suffix.lower()}"
                counter += 1

        yield f"RENAME '{path.name}' -> '{dest.name}'"
        if not dry_run:
            path.rename(dest)


# ---------------------------------------------------------------------------
# 5. Acoustic fingerprint lookup (AcoustID + MusicBrainz + Cover Art Archive)
# ---------------------------------------------------------------------------

def enrich_with_lookup(
    root: Path,
    api_key: str,
    *,
    overwrite: bool = False,
    fetch_art: bool = True,
    ignore: set[str] | None = None,
    dry_run: bool = False,
) -> Iterator[str]:
    from soundwave.lookup import enrich_file

    files = _collect_audio(root, ignore=ignore)
    total = len(files)

    for i, path in enumerate(files, 1):
        prefix = f"[{i}/{total}]"
        result = enrich_file(
            path,
            api_key,
            overwrite=overwrite,
            fetch_art=fetch_art,
            dry_run=dry_run,
        )
        status = result["status"]

        if status == "skipped":
            yield f"{prefix} SKIP (already complete): {path.name}"
        elif status == "no_match":
            yield f"{prefix} NO MATCH: {path.name}"
        elif status == "error":
            yield f"{prefix} ERROR '{path.name}': {result.get('error')}"
        elif status == "matched":
            if result["changes"]:
                change_str = ", ".join(result["changes"])
                tag = "[dry run] " if dry_run else ""
                yield f"{prefix} {tag}MATCHED '{path.name}': {change_str}"
            else:
                yield f"{prefix} MATCHED (nothing new): {path.name}"
