"""
Rekordbox XML interchange format handler.

Workflow:
  1. Optionally load an existing Rekordbox XML export as a base.
  2. For each analyzed track, upsert a <TRACK> element with <POSITION_MARK> children.
  3. Save the output XML.
  4. User imports it into Rekordbox via File → Import Collection in xml format.

IMPORTANT — Rekordbox "existing track" limitation:
  If a track is already in your Rekordbox library, importing the XML will NOT
  update its hot cues. You must delete the track from Rekordbox first, then
  import the XML to pick up the new cues.
  See README § Importing for the exact steps.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote, unquote
from xml.dom import minidom

from soundwave.rekordbox.models import CuePoint, TrackAnalysis


# ---------------------------------------------------------------------------
# URI ↔ path helpers
# ---------------------------------------------------------------------------

def path_to_uri(path: str) -> str:
    """Convert an absolute OS path to a Rekordbox-compatible file URI.

    Rekordbox's XML spec requires the 'file://localhost/' form (not plain
    'file:///') — without the 'localhost' segment, Rekordbox can't resolve
    the location and silently imports nothing.
    """
    p = Path(path).resolve()
    # Windows: C:\Music\foo.mp3  →  file://localhost/C:/Music/foo.mp3
    parts = p.parts
    if len(parts) > 0 and parts[0].endswith(":\\"):
        drive = parts[0].rstrip("\\")
        rest = "/".join(quote(part, safe="") for part in parts[1:])
        return f"file://localhost/{drive}/{rest}"
    # POSIX
    encoded = "/".join(quote(part, safe="") for part in parts[1:])
    return f"file://localhost/{encoded}"


def uri_to_path(uri: str) -> str:
    """Convert a Rekordbox file URI back to an OS path.

    Accepts both the spec-correct 'file://localhost/' form and the plain
    'file:///' form (older Soundwave output, or XML from other tools).
    """
    for prefix in ("file://localhost/", "file:///"):
        if uri.startswith(prefix):
            raw = unquote(uri[len(prefix):])
            # Windows drive letter
            if len(raw) > 1 and raw[1] == ":":
                return raw.replace("/", "\\")
            return "/" + raw
    return uri


# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------

def _split_rgb(color: int):
    return (color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF


# ---------------------------------------------------------------------------
# XML generation
# ---------------------------------------------------------------------------

def _prettify(element: ET.Element) -> str:
    """Return pretty-printed XML string with XML declaration."""
    raw = ET.tostring(element, encoding="unicode")
    reparsed = minidom.parseString(raw)
    pretty = reparsed.toprettyxml(indent="  ", encoding=None)
    # minidom adds its own declaration — strip it, we'll add a clean one
    lines = pretty.split("\n")
    if lines[0].startswith("<?xml"):
        lines = lines[1:]
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + "\n".join(lines)


def build_xml(
    analyses: List[TrackAnalysis],
    base_xml_path: Optional[str] = None,
    playlist_name: Optional[str] = None,
) -> ET.Element:
    """
    Build a DJ_PLAYLISTS XML tree from a list of TrackAnalysis results.

    If base_xml_path is provided, load it and merge/upsert tracks into it.
    Otherwise, create a fresh tree.
    If playlist_name is given, add all analyzed tracks to that playlist.
    """
    if base_xml_path and Path(base_xml_path).exists():
        tree = ET.parse(base_xml_path)
        root = tree.getroot()
        collection = root.find("COLLECTION")
        if collection is None:
            collection = ET.SubElement(root, "COLLECTION", Entries="0")
        # Build lookup: resolved OS path → existing TRACK element. Keyed by
        # path (via uri_to_path) rather than the raw Location string so a
        # base XML written with the older, non-spec-compliant 'file:///'
        # form (missing 'localhost') still matches and gets updated in
        # place instead of duplicated.
        existing: Dict[str, ET.Element] = {
            uri_to_path(el.get("Location", "")): el
            for el in collection.findall("TRACK")
        }
    else:
        root = ET.Element("DJ_PLAYLISTS", Version="1.0.0")
        ET.SubElement(root, "PRODUCT", Name="rekordbox", Version="6.0.0", Company="AlphaTheta")
        collection = ET.SubElement(root, "COLLECTION", Entries="0")
        playlists = ET.SubElement(root, "PLAYLISTS")
        ET.SubElement(playlists, "NODE", Type="0", Name="ROOT", Count="0")
        existing = {}

    track_id_counter = _max_track_id(collection) + 1
    analyzed_track_ids: List[int] = []

    for analysis in analyses:
        uri = path_to_uri(analysis.audio_path)
        resolved_path = str(Path(analysis.audio_path).resolve())

        if resolved_path in existing:
            track_el = existing[resolved_path]
            _remove_soundwave_marks(track_el)
            track_el.set("Location", uri)  # upgrade to spec-correct URI form
        else:
            track_el = ET.SubElement(collection, "TRACK")
            track_el.set("TrackID", str(track_id_counter))
            track_el.set("Location", uri)
            track_id_counter += 1

        track_el.set("Name", analysis.title)
        track_el.set("Artist", analysis.artist)
        track_el.set("TotalTime", str(int(analysis.duration_sec)))
        track_el.set("AverageBpm", f"{analysis.bpm:.2f}")

        for slot, cue in enumerate(analysis.cues):
            r, g, b = _split_rgb(cue.color)
            mark = ET.SubElement(track_el, "POSITION_MARK")
            mark.set("Name", cue.label)
            mark.set("Type", "0")              # 0 = hot cue
            mark.set("Start", f"{cue.time_sec:.3f}")
            mark.set("Num", str(slot))         # 0=A … 7=H
            mark.set("Red", str(r))
            mark.set("Green", str(g))
            mark.set("Blue", str(b))
            mark.set("Generator", "soundwave") # marker so we can remove on re-run

        analyzed_track_ids.append(int(track_el.get("TrackID", 0)))

    collection.set("Entries", str(len(collection.findall("TRACK"))))

    if playlist_name and analyzed_track_ids:
        add_playlist(root, playlist_name, analyzed_track_ids)

    return root


def save_xml(root: ET.Element, output_path: str) -> None:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(_prettify(root), encoding="utf-8")


# ---------------------------------------------------------------------------
# Reading back
# ---------------------------------------------------------------------------

def load_tracks_from_xml(xml_path: str) -> List[dict]:
    """Return a list of track dicts from a Rekordbox XML file (for display/debug)."""
    tree = ET.parse(xml_path)
    root = tree.getroot()
    collection = root.find("COLLECTION")
    if collection is None:
        return []
    tracks = []
    for el in collection.findall("TRACK"):
        marks = []
        for m in el.findall("POSITION_MARK"):
            marks.append({
                "label": m.get("Name", ""),
                "time_sec": float(m.get("Start", 0)),
                "slot": int(m.get("Num", -1)),
                "type": int(m.get("Type", 0)),
            })
        tracks.append({
            "title": el.get("Name", ""),
            "artist": el.get("Artist", ""),
            "location": el.get("Location", ""),
            "bpm": el.get("AverageBpm", ""),
            "cues": marks,
        })
    return tracks


def load_tracks_from_live_db() -> List[dict]:
    """
    Return track dicts read directly from the local Rekordbox database
    (read-only) via pyrekordbox — no manual "Export Collection in xml
    format" step needed.

    Raises ImportError if pyrekordbox isn't installed, or RuntimeError if
    the database can't be opened (Rekordbox not installed, or — rarely —
    locked by a running Rekordbox instance).
    """
    try:
        from pyrekordbox import Rekordbox6Database
    except ImportError as exc:
        raise ImportError(
            "pyrekordbox is not installed. Run: pip install pyrekordbox"
        ) from exc

    try:
        db = Rekordbox6Database()
        tracks = []
        for c in db.get_content():
            if not c.FolderPath:
                continue
            tracks.append({
                "title": c.Title or "",
                "artist": c.ArtistName or "",
                "location": path_to_uri(c.FolderPath),
                "bpm": str(c.BPM) if c.BPM else "",
                "cues": [],
            })
        return tracks
    except Exception as exc:
        raise RuntimeError(
            "Could not read the Rekordbox database. Make sure Rekordbox is "
            "installed. If it's currently open, try closing it and retry."
        ) from exc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _max_track_id(collection: ET.Element) -> int:
    ids = [int(el.get("TrackID", 0)) for el in collection.findall("TRACK")]
    return max(ids, default=0)


def _remove_soundwave_marks(track_el: ET.Element) -> None:
    """Remove only POSITION_MARKs we generated (Generator="soundwave")."""
    to_remove = [
        m for m in track_el.findall("POSITION_MARK")
        if m.get("Generator") == "soundwave"
    ]
    for m in to_remove:
        track_el.remove(m)


# ---------------------------------------------------------------------------
# Playlist helpers
# ---------------------------------------------------------------------------

def _get_root_playlist_node(root: ET.Element) -> ET.Element:
    """Return the PLAYLISTS > ROOT node, creating it if absent."""
    playlists_el = root.find("PLAYLISTS")
    if playlists_el is None:
        playlists_el = ET.SubElement(root, "PLAYLISTS")
    root_node = playlists_el.find("NODE[@Name='ROOT']")
    if root_node is None:
        root_node = ET.SubElement(playlists_el, "NODE", Type="0", Name="ROOT", Count="0")
    return root_node


def add_playlist(
    root: ET.Element,
    name: str,
    track_ids: List[int],
    folder: Optional[str] = None,
) -> None:
    """
    Add or replace a playlist node inside ROOT (or inside a named folder).

    Parameters
    ----------
    root       : DJ_PLAYLISTS root element.
    name       : Playlist name shown in Rekordbox.
    track_ids  : Ordered list of TrackID integers referencing COLLECTION entries.
    folder     : If given, nest the playlist inside a folder with this name.
    """
    root_node = _get_root_playlist_node(root)

    parent = root_node
    if folder:
        folder_node = next(
            (n for n in root_node.findall("NODE")
             if n.get("Name") == folder and n.get("Type") == "0"),
            None,
        )
        if folder_node is None:
            folder_node = ET.SubElement(root_node, "NODE", Type="0", Name=folder, Count="0")
        parent = folder_node

    # Replace existing playlist with the same name
    existing = next(
        (n for n in parent.findall("NODE")
         if n.get("Name") == name and n.get("Type") == "1"),
        None,
    )
    if existing is not None:
        parent.remove(existing)

    playlist_node = ET.SubElement(
        parent, "NODE", Type="1", Name=name, KeyType="0", Entries=str(len(track_ids))
    )
    for tid in track_ids:
        ET.SubElement(playlist_node, "TRACK", Key=str(tid))

    parent.set("Count", str(len(parent.findall("NODE"))))
    if folder:
        root_node.set("Count", str(len(root_node.findall("NODE"))))


def upsert_tracks_minimal(
    root: ET.Element,
    audio_paths: List[str],
) -> List[int]:
    """
    Ensure every audio path has a TRACK entry in the COLLECTION.

    Tracks already present are left untouched; new ones get a minimal entry
    (Location + basic mutagen metadata where available).  Returns the list of
    TrackIDs in the same order as audio_paths.
    """
    collection = root.find("COLLECTION")
    if collection is None:
        collection = ET.SubElement(root, "COLLECTION", Entries="0")

    existing: Dict[str, ET.Element] = {
        uri_to_path(el.get("Location", "")): el for el in collection.findall("TRACK")
    }
    counter = _max_track_id(collection) + 1
    ids: List[int] = []

    for audio_path in audio_paths:
        uri = path_to_uri(audio_path)
        resolved_path = str(Path(audio_path).resolve())
        if resolved_path in existing:
            existing[resolved_path].set("Location", uri)  # upgrade URI form
            ids.append(int(existing[resolved_path].get("TrackID", 0)))
            continue

        title, artist = _read_tags(audio_path)
        track_el = ET.SubElement(collection, "TRACK")
        track_el.set("TrackID", str(counter))
        track_el.set("Location", uri)
        track_el.set("Name", title)
        track_el.set("Artist", artist)
        existing[resolved_path] = track_el
        ids.append(counter)
        counter += 1

    collection.set("Entries", str(len(collection.findall("TRACK"))))
    return ids


def _read_tags(audio_path: str):
    """Return (title, artist) from file tags, falling back to filename."""
    try:
        from mutagen import File as MutagenFile
        tags = MutagenFile(audio_path, easy=True)
        if tags:
            title = str(tags.get("title", [Path(audio_path).stem])[0])
            artist = str(tags.get("artist", [""])[0])
            return title, artist
    except Exception:
        pass
    return Path(audio_path).stem, ""


def organize_by_bpm(
    root: ET.Element,
    step: int = 10,
    folder: str = "By BPM",
) -> None:
    """Create BPM-range playlists (e.g. '120-130 BPM') from the COLLECTION."""
    collection = root.find("COLLECTION")
    if collection is None:
        return

    buckets: Dict[str, List[int]] = defaultdict(list)
    for el in collection.findall("TRACK"):
        try:
            bpm = float(el.get("AverageBpm", 0))
            tid = int(el.get("TrackID", 0))
        except (ValueError, TypeError):
            continue
        if bpm <= 0:
            continue
        low = (int(bpm) // step) * step
        key = f"{low}-{low + step} BPM"
        buckets[key].append(tid)

    for label in sorted(buckets):
        add_playlist(root, label, buckets[label], folder=folder)


def organize_by_folder(
    root: ET.Element,
    folder: str = "By Folder",
) -> None:
    """Create per-directory playlists from COLLECTION track locations."""
    collection = root.find("COLLECTION")
    if collection is None:
        return

    buckets: Dict[str, List[int]] = defaultdict(list)
    for el in collection.findall("TRACK"):
        uri = el.get("Location", "")
        tid = int(el.get("TrackID", 0))
        dir_name = Path(uri_to_path(uri)).parent.name or "Root"
        buckets[dir_name].append(tid)

    for dir_name in sorted(buckets):
        add_playlist(root, dir_name, buckets[dir_name], folder=folder)


# ---------------------------------------------------------------------------
# Reading playlists back
# ---------------------------------------------------------------------------

def load_playlists_from_xml(xml_path: str) -> List[dict]:
    """Return a nested list describing the playlist tree in a Rekordbox XML file."""
    tree = ET.parse(xml_path)
    root = tree.getroot()
    playlists_el = root.find("PLAYLISTS")
    if playlists_el is None:
        return []
    root_node = playlists_el.find("NODE[@Name='ROOT']")
    if root_node is None:
        return []

    collection = root.find("COLLECTION") or ET.Element("COLLECTION")
    track_info: Dict[int, dict] = {
        int(el.get("TrackID", 0)): {
            "title": el.get("Name", ""),
            "artist": el.get("Artist", ""),
            "bpm": el.get("AverageBpm", ""),
        }
        for el in collection.findall("TRACK")
    }

    return _read_playlist_nodes(root_node, track_info)


def _read_playlist_nodes(
    node: ET.Element,
    track_info: Dict[int, dict],
    depth: int = 0,
) -> List[dict]:
    results = []
    for child in node.findall("NODE"):
        name = child.get("Name", "")
        if child.get("Type") == "0":
            results.append({
                "type": "folder",
                "name": name,
                "depth": depth,
                "children": _read_playlist_nodes(child, track_info, depth + 1),
            })
        else:
            tracks = [
                {**track_info.get(int(t.get("Key", 0)), {}), "id": int(t.get("Key", 0))}
                for t in child.findall("TRACK")
            ]
            results.append({"type": "playlist", "name": name, "depth": depth, "tracks": tracks})
    return results
