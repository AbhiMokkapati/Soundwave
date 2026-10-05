"""
Soundwave local web app: a FastAPI backend wrapping the existing CLI
modules (library, analyzer, similarity, lyrics, rekordbox XML) behind a
browser UI, so day-to-day use doesn't require remembering CLI flags.

Runs bound to localhost only — this is a single-user local tool, not a
service meant to be exposed on the network.
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from soundwave.analyzer import analyze_track
from soundwave.library import (
    clear_genre_from_folders, dedupe_by_title, enrich_with_lookup,
    fill_missing_tags, rename_to_track_name, sort_by_genre,
)
from soundwave.lyrics import fetch_lyrics
from soundwave.rekordbox.models import CuePoint, TrackAnalysis
from soundwave.rekordbox.xml_handler import (
    add_playlist, build_xml, load_playlists_from_xml, load_tracks_from_live_db,
    load_tracks_from_xml, save_xml, upsert_tracks_minimal,
)
from soundwave.similarity import (
    cluster_summary, cluster_tracks, extract_features, similarity_path_order,
)

AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".aiff", ".aif", ".m4a", ".ogg"}
_MEDIA_TYPES = {
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac",
    ".aiff": "audio/aiff", ".aif": "audio/aiff", ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
}

APP_DIR = Path.home() / ".soundwave"
APP_DIR.mkdir(exist_ok=True)
CONFIG_PATH = APP_DIR / "config.json"
ANALYSIS_CACHE_PATH = APP_DIR / "analysis_cache.json"
WAVEFORM_CACHE_DIR = APP_DIR / "waveform_cache"
LYRICS_CACHE_DIR = APP_DIR / "lyrics_cache"
SIMILARITY_CACHE_PATH = APP_DIR / "similarity_features.json"

app = FastAPI(title="Soundwave")

HOST, PORT = "127.0.0.1", 8765
_ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}", f"[::1]:{PORT}"}


@app.middleware("http")
async def _local_only_guard(request: Request, call_next):
    """This API can read, rename and delete files by path with no login, so
    it must only ever be reachable by the local user's own UI. Reject
    requests whose Host header isn't a loopback name (DNS rebinding) and
    any request a *different* website's page triggered (CSRF — e.g. an
    <img> pointing at /api/tidy/stream), identified by the browser's
    Sec-Fetch-Site / Origin headers."""
    if request.headers.get("host", "").lower() not in _ALLOWED_HOSTS:
        return JSONResponse({"error": "Forbidden host"}, status_code=403)
    if request.headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
        return JSONResponse({"error": "Cross-site requests are not allowed"}, status_code=403)
    origin = request.headers.get("origin")
    if origin and origin.lower() not in {f"http://{h}" for h in _ALLOWED_HOSTS}:
        return JSONResponse({"error": "Cross-origin requests are not allowed"}, status_code=403)
    return await call_next(request)


# ---------------------------------------------------------------------------
# Config persistence (remembers last library path, AcoustID key, etc.)
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


@app.get("/api/config")
def get_config():
    return _load_config()


@app.post("/api/config")
def post_config(patch: dict):
    cfg = _load_config()
    # Plain string/number/bool settings only; ignore anything nested.
    cfg.update({k: v for k, v in patch.items()
                if isinstance(k, str) and isinstance(v, (str, int, float, bool, type(None)))})
    _save_config(cfg)
    return cfg


# ---------------------------------------------------------------------------
# Library browsing
# ---------------------------------------------------------------------------

def _read_tags_summary(path: Path) -> dict:
    try:
        from mutagen import File as MutagenFile
        tags = MutagenFile(str(path), easy=True)
    except Exception:
        tags = None
    def g(key):
        if tags is None:
            return None
        v = tags.get(key)
        return v[0] if v else None
    return {
        "title": g("title") or path.stem,
        "artist": g("artist"),
        "genre": g("genre"),
        "bpm": g("bpm"),
    }


@app.get("/api/pick-folder")
def pick_folder(initial: Optional[str] = None):
    """Opens a native OS folder-picker dialog on the machine running the
    server. Only makes sense for local, single-user use (which is how this
    app runs) — a browser can't be handed a real filesystem path by its own
    file/folder inputs (webkitdirectory only exposes relative names, never
    an absolute path), so the picker has to run server-side instead. Runs
    in a subprocess because Tk needs to own the main thread, which the
    uvicorn process already doesn't."""
    import subprocess
    script = (
        "import tkinter, sys\n"
        "from tkinter import filedialog\n"
        "root = tkinter.Tk()\n"
        "root.withdraw()\n"
        "root.attributes('-topmost', True)\n"
        f"path = filedialog.askdirectory(initialdir={initial!r} or None, title='Select a music folder')\n"
        "sys.stdout.write(path)\n"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        return JSONResponse({"error": "Folder picker timed out"}, status_code=504)
    path = result.stdout.strip()
    if not path:
        return {"path": None}  # user cancelled
    return {"path": path}


@app.get("/api/browse")
def browse(path: Optional[str] = None):
    cfg = _load_config()
    root = Path(path) if path else Path(cfg.get("last_library_path", str(Path.home() / "Music")))
    if not root.exists() or not root.is_dir():
        return JSONResponse({"error": f"Not a directory: {root}"}, status_code=400)

    cfg["last_library_path"] = str(root)
    _save_config(cfg)

    folders = []
    files = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if entry.is_dir():
            try:
                n_audio = sum(
                    1 for f in entry.rglob("*")
                    if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS
                )
            except Exception:
                n_audio = 0
            folders.append({"name": entry.name, "path": str(entry), "track_count": n_audio})
        elif entry.suffix.lower() in AUDIO_EXTENSIONS:
            info = _read_tags_summary(entry)
            files.append({"name": entry.name, "path": str(entry), **info})

    return {
        "path": str(root),
        "parent": str(root.parent) if root.parent != root else None,
        "folders": folders,
        "files": files,
    }


# ---------------------------------------------------------------------------
# Tidy operations (checkboxes instead of CLI flags), streamed over SSE
# ---------------------------------------------------------------------------

def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


def _notify_windows(title: str, message: str) -> None:
    """Best-effort Windows toast notification so a long batch run (analysis,
    similarity) can finish while the browser tab is backgrounded or the user
    has stepped away entirely. Runs in a background thread — win11toast's
    toast() blocks on a WinRT message loop waiting for the toast's dismissal
    callback, and this fires from inside a streaming-response generator
    where blocking would stall the SSE stream itself. Never raises: a
    notification failure (module missing, WinRT unavailable) shouldn't fail
    the actual analysis request."""
    if sys.platform != "win32":
        return
    import threading

    def _fire():
        try:
            from win11toast import toast
            toast(title, message, duration="long")
        except Exception:
            pass

    threading.Thread(target=_fire, daemon=True).start()


@app.get("/api/tidy/stream")
def tidy_stream(
    path: str,
    fill_tags: bool = False,
    fill_bpm: bool = False,
    lookup: bool = False,
    acoustid_key: Optional[str] = None,
    overwrite: bool = False,
    no_art: bool = False,
    sort_genres: bool = False,
    dedupe: bool = False,
    rename: bool = False,
    clear_genre: List[str] = Query(default=[]),
    ignore: List[str] = Query(default=[]),
    dry_run: bool = False,
):
    root = Path(path)

    def gen():
        if not root.is_dir():
            yield _sse({"type": "error", "message": f"Not a directory: {path}"})
            return

        ignore_set = set(ignore) if ignore else None

        try:
            if clear_genre:
                yield _sse({"type": "step", "label": "Clearing bogus genre tags"})
                for line in clear_genre_from_folders(root, set(clear_genre), ignore=ignore_set, dry_run=dry_run):
                    yield _sse({"type": "line", "text": line})

            if fill_tags:
                yield _sse({"type": "step", "label": "Filling missing tags"})
                for line in fill_missing_tags(root, fill_bpm=fill_bpm, ignore=ignore_set, dry_run=dry_run):
                    yield _sse({"type": "line", "text": line})

            if lookup:
                if not acoustid_key:
                    yield _sse({"type": "error", "message": "AcoustID key is required for lookup"})
                else:
                    yield _sse({"type": "step", "label": "Fingerprint lookup (AcoustID -> MusicBrainz)"})
                    for line in enrich_with_lookup(
                        root, acoustid_key, overwrite=overwrite, fetch_art=not no_art,
                        ignore=ignore_set, dry_run=dry_run,
                    ):
                        yield _sse({"type": "line", "text": line})

            if dedupe:
                yield _sse({"type": "step", "label": "Deduplicating by title"})
                for line in dedupe_by_title(root, ignore=ignore_set, dry_run=dry_run):
                    yield _sse({"type": "line", "text": line})

            if sort_genres:
                yield _sse({"type": "step", "label": "Sorting into genre folders"})
                for line in sort_by_genre(root, ignore=ignore_set, dry_run=dry_run):
                    yield _sse({"type": "line", "text": line})

            if rename:
                yield _sse({"type": "step", "label": "Renaming files to track titles"})
                for line in rename_to_track_name(root, ignore=ignore_set, dry_run=dry_run):
                    yield _sse({"type": "line", "text": line})

            yield _sse({"type": "done"})
        except Exception as exc:
            yield _sse({"type": "error", "message": str(exc)})

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Track analysis / cue points
# ---------------------------------------------------------------------------

def _analysis_cache() -> dict:
    if ANALYSIS_CACHE_PATH.exists():
        try:
            return json.loads(ANALYSIS_CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_analysis_cache(cache: dict) -> None:
    ANALYSIS_CACHE_PATH.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def _analyze_one(p: Path, use_demucs: bool, recompute: bool, cache: dict) -> dict:
    """Analyze a single file, using/populating the given cache dict in place.
    Caller owns loading/saving the cache to disk (batch callers do this once
    around a whole loop instead of per file)."""
    mtime = p.stat().st_mtime
    key = f"{p.resolve()}::{use_demucs}::{mtime}"
    if not recompute and key in cache:
        return cache[key]

    result = analyze_track(str(p), use_demucs=use_demucs)
    payload = {
        "title": result.title,
        "artist": result.artist,
        "bpm": result.bpm,
        "duration_sec": result.duration_sec,
        "cues": [
            {"label": c.label, "time_sec": c.time_sec, "color": c.color}
            for c in result.cues
        ],
    }
    cache[key] = payload
    return payload


@app.get("/api/track/analyze")
def track_analyze(path: str, use_demucs: bool = False, recompute: bool = False):
    p = Path(path)
    if p.suffix.lower() not in AUDIO_EXTENSIONS:
        return JSONResponse({"error": "Not an audio file"}, status_code=400)
    if not p.is_file():
        return JSONResponse({"error": f"File not found: {path}"}, status_code=404)

    cache = _analysis_cache()
    payload = _analyze_one(p, use_demucs, recompute, cache)
    _save_analysis_cache(cache)
    return payload


@app.get("/api/analyze/stream")
def analyze_stream(
    path: str,
    output: str = "soundwave_output.xml",
    base_xml: Optional[str] = None,
    use_demucs: bool = False,
    recompute: bool = False,
):
    """Analyze every audio file under `path` and write cues for all of them
    into one Rekordbox XML, saved incrementally after each track so a long
    batch run isn't lost if interrupted partway through."""

    def gen():
        if Path(output).suffix.lower() != ".xml":
            yield _sse({"type": "error", "message": "Output must be a .xml file"})
            return
        files = _collect_audio_files(path)
        if not files:
            yield _sse({"type": "error", "message": f"No audio files found at: {path}"})
            return

        yield _sse({"type": "step", "label": f"Found {len(files)} audio file(s). Analyzing..."})

        cache = _analysis_cache()
        failed: List[str] = []
        total_cues = 0

        for i, fpath in enumerate(files, 1):
            name = Path(fpath).name
            try:
                payload = _analyze_one(Path(fpath), use_demucs, recompute, cache)
            except Exception as exc:
                failed.append(name)
                yield _sse({"type": "line", "text": f"[{i}/{len(files)}] ERROR '{name}': {exc}"})
                continue

            analysis = TrackAnalysis(
                audio_path=str(Path(fpath).resolve()),
                title=payload["title"],
                artist=payload["artist"],
                duration_sec=payload["duration_sec"],
                bpm=payload["bpm"],
                cues=[CuePoint(c["label"], c["time_sec"], c["color"]) for c in payload["cues"]],
            )
            base = base_xml if base_xml and Path(base_xml).exists() else (
                output if Path(output).exists() else None
            )
            root = build_xml([analysis], base_xml_path=base)
            save_xml(root, output)
            total_cues += len(payload["cues"])

            yield _sse({
                "type": "progress", "index": i, "total": len(files),
                "name": name, "cueCount": len(payload["cues"]),
            })

        _save_analysis_cache(cache)

        analyzed = len(files) - len(failed)
        _notify_windows(
            "Soundwave: analysis complete",
            f"Analyzed {analyzed}/{len(files)} track(s)" + (f", {len(failed)} failed" if failed else ""),
        )
        yield _sse({
            "type": "done", "output": str(Path(output).resolve()),
            "analyzed": analyzed, "failed": failed, "totalCues": total_cues,
        })

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/track/audio")
def track_audio(path: str):
    p = Path(path)
    # Only ever serve audio files, never arbitrary files from disk.
    if p.suffix.lower() not in AUDIO_EXTENSIONS:
        return JSONResponse({"error": "Not an audio file"}, status_code=400)
    if not p.is_file():
        return JSONResponse({"error": f"File not found: {path}"}, status_code=404)
    media_type = _MEDIA_TYPES.get(p.suffix.lower(), "application/octet-stream")
    return FileResponse(str(p), media_type=media_type)


@app.get("/api/track/waveform")
def track_waveform(path: str, points: int = 1000):
    import hashlib
    import numpy as np
    import librosa

    p = Path(path)
    if p.suffix.lower() not in AUDIO_EXTENSIONS:
        return JSONResponse({"error": "Not an audio file"}, status_code=400)
    if not p.is_file():
        return JSONResponse({"error": f"File not found: {path}"}, status_code=404)
    points = max(10, min(points, 5000))

    WAVEFORM_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    mtime = p.stat().st_mtime
    cache_key = hashlib.sha1(f"{p.resolve()}::{mtime}::{points}".encode()).hexdigest()
    cache_file = WAVEFORM_CACHE_DIR / f"{cache_key}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))

    y, sr = librosa.load(str(p), sr=22050, mono=True)
    duration = float(len(y) / sr)
    chunk = max(1, len(y) // points)
    peaks = [
        float(np.max(np.abs(y[i:i + chunk]))) if len(y[i:i + chunk]) else 0.0
        for i in range(0, len(y), chunk)
    ][:points]

    payload = {"peaks": peaks, "duration": duration}
    cache_file.write_text(json.dumps(payload), encoding="utf-8")
    return payload


class CueIn(BaseModel):
    label: str
    time_sec: float
    color: int = 0xFF6400


class SaveCuesRequest(BaseModel):
    path: str
    cues: List[CueIn]
    output: str
    base_xml: Optional[str] = None
    bpm: Optional[float] = None
    duration_sec: Optional[float] = None


@app.post("/api/track/cues")
def save_cues(req: SaveCuesRequest):
    p = Path(req.path)
    if p.suffix.lower() not in AUDIO_EXTENSIONS:
        return JSONResponse({"error": "Not an audio file"}, status_code=400)
    if not p.is_file():
        return JSONResponse({"error": f"File not found: {req.path}"}, status_code=404)
    if Path(req.output).suffix.lower() != ".xml":
        return JSONResponse({"error": "Output must be a .xml file"}, status_code=400)

    info = _read_tags_summary(p)
    duration = req.duration_sec
    if duration is None:
        import librosa
        duration = librosa.get_duration(path=str(p))

    bpm = req.bpm
    if bpm is None:
        bpm = float(info["bpm"]) if info["bpm"] else 0.0

    analysis = TrackAnalysis(
        audio_path=str(p.resolve()),
        title=info["title"] or p.stem,
        artist=info["artist"] or "Unknown",
        duration_sec=duration,
        bpm=bpm,
        cues=[CuePoint(c.label, c.time_sec, c.color) for c in req.cues],
    )

    base = req.base_xml if req.base_xml and Path(req.base_xml).exists() else (
        req.output if Path(req.output).exists() else None
    )
    root = build_xml([analysis], base_xml_path=base)
    save_xml(root, req.output)
    return {"status": "ok", "output": str(Path(req.output).resolve())}


# ---------------------------------------------------------------------------
# Lyrics
# ---------------------------------------------------------------------------

@app.get("/api/lyrics")
def lyrics(artist: str, title: str, duration: Optional[float] = None):
    return fetch_lyrics(artist, title, duration, cache_dir=LYRICS_CACHE_DIR)


# ---------------------------------------------------------------------------
# Similarity playlists
# ---------------------------------------------------------------------------

def _collect_audio_files(path: str) -> List[str]:
    p = Path(path)
    if p.is_file():
        return [str(p)] if p.suffix.lower() in AUDIO_EXTENSIONS else []
    return sorted(
        str(f) for f in p.rglob("*")
        if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS
    )


@app.get("/api/similarity/stream")
def similarity_stream(
    path: str,
    clusters: int = 6,
    mode: str = "both",
    analysis_sec: float = 60.0,
    output: str = "soundwave_output.xml",
    recompute: bool = False,
):
    import queue
    import threading

    def gen():
        if Path(output).suffix.lower() != ".xml":
            yield _sse({"type": "error", "message": "Output must be a .xml file"})
            return
        files = _collect_audio_files(path)
        if not files:
            yield _sse({"type": "error", "message": f"No audio files found at: {path}"})
            return

        yield _sse({"type": "step", "label": f"Found {len(files)} audio file(s). Extracting features..."})

        q: "queue.Queue" = queue.Queue()
        result_holder = {}

        def worker():
            def on_progress(i, total, fpath, cached):
                q.put(("progress", i, total, Path(fpath).name, cached))
            try:
                feats = extract_features(
                    files, SIMILARITY_CACHE_PATH, analysis_sec=analysis_sec,
                    recompute=recompute, on_progress=on_progress,
                )
                result_holder["features"] = feats
            except Exception as exc:
                result_holder["error"] = str(exc)
            q.put(("finished",))

        t = threading.Thread(target=worker, daemon=True)
        t.start()

        while True:
            item = q.get()
            if item[0] == "progress":
                _, i, total, name, cached = item
                yield _sse({"type": "progress", "index": i, "total": total, "name": name, "cached": cached})
            elif item[0] == "finished":
                break
        t.join()

        if "error" in result_holder:
            yield _sse({"type": "error", "message": result_holder["error"]})
            return

        features = result_holder["features"]
        if len(features) < 2:
            yield _sse({"type": "error", "message": "Not enough analyzable tracks to build playlists."})
            return

        if Path(output).exists():
            xml_root = ET.parse(output).getroot()
        else:
            xml_root = build_xml([])

        analyzed_paths = list(features.keys())
        track_ids = upsert_tracks_minimal(xml_root, analyzed_paths)
        id_by_path = dict(zip(analyzed_paths, track_ids))

        summary = {"clusters": [], "path": None}

        if mode in ("clusters", "both"):
            groups = cluster_tracks(features, clusters)
            for i, group_paths in groups.items():
                name = f"Similarity {i + 1} ({cluster_summary(features, group_paths)})"
                ids = [id_by_path[p] for p in group_paths]
                add_playlist(xml_root, name, ids, folder="Similarity")
                summary["clusters"].append({"name": name, "count": len(ids)})
                yield _sse({"type": "line", "text": f"Built '{name}' — {len(ids)} track(s)"})

        if mode in ("path", "both"):
            ordered_paths = similarity_path_order(features)
            ids = [id_by_path[p] for p in ordered_paths]
            add_playlist(xml_root, "DJ Set Path", ids, folder="Similarity")
            summary["path"] = len(ids)
            yield _sse({"type": "line", "text": f"Built 'DJ Set Path' — {len(ids)} track(s)"})

        save_xml(xml_root, output)
        _notify_windows("Soundwave: similarity playlists built", f"{len(analyzed_paths)} track(s) processed")
        yield _sse({"type": "done", "output": str(Path(output).resolve()), "summary": summary})

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Playlist viewer
# ---------------------------------------------------------------------------

@app.get("/api/playlists")
def playlists(xml_path: str):
    if Path(xml_path).suffix.lower() != ".xml":
        return JSONResponse({"error": "Not an XML file"}, status_code=400)
    if not Path(xml_path).exists():
        return JSONResponse({"error": f"File not found: {xml_path}"}, status_code=404)
    return {"nodes": load_playlists_from_xml(xml_path)}


# ---------------------------------------------------------------------------
# Rekordbox re-import checklist
#
# Rekordbox silently refuses to update hot cues on a track it already
# recognizes as being in the collection (matched by file Location) — a
# plain XML re-import does nothing for those. This compares our output XML
# against a user-exported Rekordbox collection XML (File -> Export
# Collection in xml format) to split saved tracks into "already there,
# needs remove-then-reimport" vs "new, will just import".
# ---------------------------------------------------------------------------

@app.get("/api/reimport-check")
def reimport_check(output: str, rekordbox_xml: Optional[str] = None, live: bool = False):
    for candidate in (output, rekordbox_xml):
        if candidate and Path(candidate).suffix.lower() != ".xml":
            return JSONResponse({"error": "Not an XML file"}, status_code=400)
    if not Path(output).exists():
        return JSONResponse({"error": f"File not found: {output}"}, status_code=404)

    if live:
        try:
            rb_tracks = load_tracks_from_live_db()
        except (ImportError, RuntimeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)
    else:
        if not rekordbox_xml:
            return JSONResponse({"error": "rekordbox_xml is required when live=false"}, status_code=400)
        if not Path(rekordbox_xml).exists():
            return JSONResponse({"error": f"File not found: {rekordbox_xml}"}, status_code=404)
        rb_tracks = load_tracks_from_xml(rekordbox_xml)

    our_tracks = load_tracks_from_xml(output)
    rb_locations = {t["location"] for t in rb_tracks}

    existing: List[dict] = []
    new: List[dict] = []
    for t in our_tracks:
        entry = {
            "title": t["title"], "artist": t["artist"],
            "location": t["location"], "cue_count": len(t["cues"]),
        }
        (existing if t["location"] in rb_locations else new).append(entry)

    return {"existing": existing, "new": new}


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


def _port_in_use(host: str, port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


if __name__ == "__main__":
    import webbrowser
    import uvicorn

    # If something's already listening here (most often a previous run that
    # wasn't closed cleanly, e.g. the terminal window was closed with the X
    # instead of Ctrl+C, orphaning the process) starting a second server
    # fails immediately with "address already in use" — with no console
    # window kept open to show that, it just looks like a silent hang and
    # the browser tab that was launched alongside it can't connect to
    # anything. Detect that up front and reuse the existing server instead.
    if _port_in_use(HOST, PORT):
        print(f"Soundwave already appears to be running at http://{HOST}:{PORT} - opening it instead of starting a second copy.")
        webbrowser.open(f"http://{HOST}:{PORT}")
    else:
        # Open the browser only once the server is confirmed to be accepting
        # connections, rather than racing it from run_app.bat — avoids the
        # "page can't be reached" that shows up if the tab opens before
        # uvicorn has finished starting up.
        import threading

        def _open_when_ready():
            import time
            for _ in range(100):  # up to ~10s
                if _port_in_use(HOST, PORT):
                    webbrowser.open(f"http://{HOST}:{PORT}")
                    return
                time.sleep(0.1)

        threading.Thread(target=_open_when_ready, daemon=True).start()
        try:
            uvicorn.run(app, host=HOST, port=PORT)
        except OSError as e:
            print(f"\nSoundwave couldn't start: {e}")
            print(f"Something else may be using port {PORT}. Close any other Soundwave windows and try again.")
            input("Press Enter to close...")
