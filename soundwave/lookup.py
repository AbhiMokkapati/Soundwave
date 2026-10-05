"""
Acoustic fingerprinting + metadata / album art lookup via free services:

  AcoustID           https://acoustid.org          free API key required
  MusicBrainz        https://musicbrainz.org       no key, 1 req/sec rate limit
  Cover Art Archive  https://coverartarchive.org   no key required
"""

from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Optional

import requests

MB_BASE = "https://musicbrainz.org/ws/2"
_MB_USER_AGENT = "Soundwave/1.0.0 (soundwave-dj-tool)"
_last_mb_call: float = 0.0


# ---------------------------------------------------------------------------
# MusicBrainz helper
# ---------------------------------------------------------------------------

def _mb_get(endpoint: str, **params) -> dict:
    """Rate-limited (1 req/sec) MusicBrainz JSON request."""
    global _last_mb_call
    wait = 1.05 - (time.monotonic() - _last_mb_call)
    if wait > 0:
        time.sleep(wait)
    params["fmt"] = "json"
    r = requests.get(
        f"{MB_BASE}/{endpoint}",
        params=params,
        headers={"User-Agent": _MB_USER_AGENT},
        timeout=15,
    )
    _last_mb_call = time.monotonic()
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# Fingerprint + AcoustID lookup
# ---------------------------------------------------------------------------

def fingerprint_lookup(path: str, api_key: str) -> Optional[dict]:
    """
    Fingerprint *path* with chromaprint / fpcalc, query AcoustID, then enrich
    via MusicBrainz.  Returns a metadata dict or None when no confident match
    is found.

    Returned keys: title, artist, album, release_id, recording_id, year, genres

    Raises RuntimeError when fpcalc is missing or the AcoustID key is invalid.
    """
    try:
        import acoustid
    except ImportError:
        raise ImportError(
            "pyacoustid is not installed.  Run:  pip install pyacoustid"
        )

    # Point pyacoustid at the fpcalc binary next to this Python interpreter.
    # pyacoustid checks the FPCALC env var before searching PATH, so set that.
    import os, sys
    _fpcalc_candidate = Path(sys.executable).parent / "fpcalc.exe"
    if _fpcalc_candidate.exists() and not os.environ.get("FPCALC"):
        os.environ["FPCALC"] = str(_fpcalc_candidate)

    # --- Step 1: fingerprint + AcoustID lookup ---
    try:
        results = list(acoustid.match(api_key, path, meta="recordings"))
    except acoustid.FingerprintGenerationError as exc:
        fpcalc_ok = os.environ.get("FPCALC") and Path(os.environ["FPCALC"]).exists()
        if fpcalc_ok:
            raise RuntimeError(
                "Audio could not be decoded for fingerprinting "
                "(file may be corrupted or use an unsupported codec)"
            ) from exc
        raise RuntimeError(
            "Could not generate a fingerprint.  Make sure fpcalc is installed "
            "and on PATH.  Download (free): https://acoustid.org/chromaprint"
        ) from exc
    except acoustid.WebServiceError as exc:
        raise RuntimeError(f"AcoustID API error: {exc}") from exc

    if not results:
        return None

    # Pick the highest-confidence result
    best = max(results, key=lambda r: r[0])
    score, recording_id, title, artist = best[0], best[1], best[2], best[3]
    if score < 0.5 or not recording_id:
        return None

    # --- Step 2: MusicBrainz – full recording details ---
    try:
        rec = _mb_get(
            f"recording/{recording_id}",
            inc="releases+artists+tags+genres",
        )
    except Exception:
        # Degrade gracefully: return what AcoustID gave us
        return {
            "title": title,
            "artist": artist,
            "album": None,
            "release_id": None,
            "recording_id": recording_id,
            "year": None,
            "genres": [],
        }

    # Artist name from MB is more reliable than AcoustID's
    artist_credits = rec.get("artist-credit") or []
    artist_name = (
        artist_credits[0].get("artist", {}).get("name") if artist_credits else None
    ) or artist

    # Pick the best official release
    releases = rec.get("releases") or []
    release = _pick_release(releases)
    release_id = release.get("id") if release else None
    album = release.get("title") if release else None
    year = None
    if release:
        date = release.get("date", "")
        year = date[:4] if date else None

    # Genres from the recording's folksonomy tags
    genres = _extract_genres(rec)

    # Fall back to the release-group tags when the recording has none
    if not genres and release_id:
        try:
            rg_id = (release or {}).get("release-group", {}).get("id")
            if rg_id:
                rg = _mb_get(f"release-group/{rg_id}", inc="tags+genres")
                genres = _extract_genres(rg)
        except Exception:
            pass

    return {
        "title": rec.get("title") or title,
        "artist": artist_name,
        "album": album,
        "release_id": release_id,
        "recording_id": recording_id,
        "year": year,
        "genres": genres,
    }


def _pick_release(releases: list) -> Optional[dict]:
    """Prefer the earliest official album release."""
    if not releases:
        return None

    def _sort_key(r):
        date = r.get("date") or "9999"
        status = r.get("status") or ""
        rtype = (r.get("release-group") or {}).get("primary-type") or ""
        return (rtype != "Album", status != "Official", date)

    return sorted(releases, key=_sort_key)[0]


def _extract_genres(obj: dict) -> list[str]:
    """Return genre/tag names from a MusicBrainz object, highest-count first."""
    items: list[tuple[int, str]] = []
    for field in ("genres", "tags"):
        for entry in obj.get(field) or []:
            name = (entry.get("name") or "").strip().title()
            count = int(entry.get("count") or 0)
            if name:
                items.append((count, name))
    items.sort(reverse=True)
    seen: set[str] = set()
    out: list[str] = []
    for _, name in items:
        key = name.lower()
        if key not in seen:
            seen.add(key)
            out.append(name)
    return out


# ---------------------------------------------------------------------------
# Cover Art Archive
# ---------------------------------------------------------------------------

def fetch_cover_art(release_id: str) -> Optional[bytes]:
    """Download the front cover (≤500 px) from Cover Art Archive.
    Returns raw JPEG bytes, or None on failure."""
    if not release_id:
        return None
    try:
        url = f"https://coverartarchive.org/release/{release_id}/front-500"
        r = requests.get(url, timeout=20, allow_redirects=True)
        if r.status_code == 200 and r.content:
            return r.content
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Tag embedding (format-aware)
# ---------------------------------------------------------------------------

def embed_tags(path: Path, meta: dict, art_bytes: Optional[bytes] = None) -> None:
    """Write *meta* fields and optional cover art into *path*.

    Only fields present in *meta* are written; existing unmentioned tags are
    left untouched.  Works for MP3, FLAC, M4A/AAC, OGG.
    """
    suffix = path.suffix.lower()
    if suffix == ".mp3":
        _embed_mp3(path, meta, art_bytes)
    elif suffix == ".flac":
        _embed_flac(path, meta, art_bytes)
    elif suffix in (".m4a", ".aac", ".mp4"):
        _embed_m4a(path, meta, art_bytes)
    elif suffix == ".ogg":
        _embed_ogg(path, meta, art_bytes)
    else:
        _embed_easy(path, meta)  # best-effort for .wav / .aiff etc.


def _embed_mp3(path: Path, meta: dict, art: Optional[bytes]) -> None:
    from mutagen.id3 import (
        APIC, ID3, ID3NoHeaderError,
        TALB, TBPM, TDRC, TCON, TIT2, TPE1,
    )
    try:
        tags = ID3(str(path))
    except ID3NoHeaderError:
        tags = ID3()
    if meta.get("title"):
        tags["TIT2"] = TIT2(encoding=3, text=meta["title"])
    if meta.get("artist"):
        tags["TPE1"] = TPE1(encoding=3, text=meta["artist"])
    if meta.get("album"):
        tags["TALB"] = TALB(encoding=3, text=meta["album"])
    if meta.get("genres"):
        tags["TCON"] = TCON(encoding=3, text=meta["genres"][0])
    if meta.get("year"):
        tags["TDRC"] = TDRC(encoding=3, text=str(meta["year"]))
    if meta.get("bpm"):
        tags["TBPM"] = TBPM(encoding=3, text=str(meta["bpm"]))
    if art:
        tags.delall("APIC")
        tags["APIC:Cover"] = APIC(
            encoding=3, mime="image/jpeg", type=3, desc="Cover", data=art,
        )
    tags.save(str(path))


def _embed_flac(path: Path, meta: dict, art: Optional[bytes]) -> None:
    from mutagen.flac import FLAC, Picture
    tags = FLAC(str(path))
    if meta.get("title"):
        tags["title"] = [meta["title"]]
    if meta.get("artist"):
        tags["artist"] = [meta["artist"]]
    if meta.get("album"):
        tags["album"] = [meta["album"]]
    if meta.get("genres"):
        tags["genre"] = [meta["genres"][0]]
    if meta.get("year"):
        tags["date"] = [str(meta["year"])]
    if meta.get("bpm"):
        tags["bpm"] = [str(meta["bpm"])]
    if art:
        pic = Picture()
        pic.type = 3          # Cover (front)
        pic.mime = "image/jpeg"
        pic.data = art
        tags.clear_pictures()
        tags.add_picture(pic)
    tags.save()


def _embed_m4a(path: Path, meta: dict, art: Optional[bytes]) -> None:
    from mutagen.mp4 import MP4, MP4Cover
    tags = MP4(str(path))
    if meta.get("title"):
        tags["\xa9nam"] = [meta["title"]]
    if meta.get("artist"):
        tags["\xa9ART"] = [meta["artist"]]
    if meta.get("album"):
        tags["\xa9alb"] = [meta["album"]]
    if meta.get("genres"):
        tags["\xa9gen"] = [meta["genres"][0]]
    if meta.get("year"):
        tags["\xa9day"] = [str(meta["year"])]
    if art:
        tags["covr"] = [MP4Cover(art, imageformat=MP4Cover.FORMAT_JPEG)]
    tags.save()


def _embed_ogg(path: Path, meta: dict, art: Optional[bytes]) -> None:
    from mutagen.oggvorbis import OggVorbis
    from mutagen.flac import Picture
    tags = OggVorbis(str(path))
    if meta.get("title"):
        tags["title"] = [meta["title"]]
    if meta.get("artist"):
        tags["artist"] = [meta["artist"]]
    if meta.get("album"):
        tags["album"] = [meta["album"]]
    if meta.get("genres"):
        tags["genre"] = [meta["genres"][0]]
    if meta.get("year"):
        tags["date"] = [str(meta["year"])]
    if art:
        pic = Picture()
        pic.type = 3
        pic.mime = "image/jpeg"
        pic.data = art
        encoded = base64.b64encode(pic.write()).decode("ascii")
        tags["metadata_block_picture"] = [encoded]
    tags.save()


def _embed_easy(path: Path, meta: dict) -> None:
    from mutagen import File as MutagenFile
    tags = MutagenFile(str(path), easy=True)
    if tags is None:
        return
    for easy_key, meta_key in [
        ("title", "title"), ("artist", "artist"),
        ("album", "album"), ("genre", "genres"),
    ]:
        val = meta.get(meta_key)
        if val:
            tags[easy_key] = [val[0]] if isinstance(val, list) else [val]
    tags.save()


# ---------------------------------------------------------------------------
# Has-art check
# ---------------------------------------------------------------------------

def has_embedded_art(path: Path) -> bool:
    """Return True when the file already contains an embedded picture."""
    try:
        suffix = path.suffix.lower()
        if suffix == ".mp3":
            from mutagen.id3 import ID3
            return any(k.startswith("APIC") for k in ID3(str(path)).keys())
        if suffix == ".flac":
            from mutagen.flac import FLAC
            return bool(FLAC(str(path)).pictures)
        if suffix in (".m4a", ".aac", ".mp4"):
            from mutagen.mp4 import MP4
            return "covr" in MP4(str(path))
        if suffix == ".ogg":
            from mutagen.oggvorbis import OggVorbis
            return "metadata_block_picture" in OggVorbis(str(path))
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# High-level: enrich a single file
# ---------------------------------------------------------------------------

def enrich_file(
    path: Path,
    api_key: str,
    *,
    overwrite: bool = False,
    fetch_art: bool = True,
    dry_run: bool = False,
) -> dict:
    """
    Fingerprint *path*, look up metadata, and write back any improvements.

    Returns a result dict:
      status   – "matched" | "no_match" | "skipped" | "error"
      changes  – list of human-readable change strings
      error    – error message (only when status=="error")
    """
    result: dict = {"path": str(path), "status": "no_match", "changes": []}

    # Decide whether we need to do anything
    try:
        from mutagen import File as MutagenFile
        existing = MutagenFile(str(path), easy=True)
    except Exception:
        existing = None

    def _has(key: str) -> bool:
        if existing is None:
            return False
        val = (existing.get(key) or [None])[0]
        return bool(val and str(val).strip() and str(val).strip().lower() not in ("", "unknown"))

    needs_meta = overwrite or not _has("title") or not _has("artist") or not _has("genre")
    try:
        _already_has_art = has_embedded_art(path)
    except Exception:
        _already_has_art = False
    needs_art = fetch_art and (overwrite or not _already_has_art)

    if not needs_meta and not needs_art:
        result["status"] = "skipped"
        return result

    # Run fingerprint lookup
    try:
        meta = fingerprint_lookup(str(path), api_key)
    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)
        return result

    if not meta:
        result["status"] = "no_match"
        return result

    result["status"] = "matched"
    result["recording_id"] = meta.get("recording_id")

    # Decide which fields to write
    to_write: dict = {}
    for key in ("title", "artist", "album", "year"):
        val = meta.get(key)
        if val and (overwrite or not _has(key)):
            to_write[key] = val
            result["changes"].append(f"{key}: '{val}'")

    if meta.get("genres") and (overwrite or not _has("genre")):
        to_write["genres"] = meta["genres"]
        result["changes"].append(f"genre: '{meta['genres'][0]}'")

    art_bytes: Optional[bytes] = None
    if needs_art and meta.get("release_id"):
        art_bytes = fetch_cover_art(meta["release_id"])
        if art_bytes:
            result["changes"].append(f"album art: {len(art_bytes) // 1024} KB JPEG")

    if not dry_run and (to_write or art_bytes):
        try:
            embed_tags(path, to_write, art_bytes)
        except Exception as exc:
            result["status"] = "error"
            result["error"] = f"tag write failed: {exc}"

    return result
