"""
Soundwave CLI — Rekordbox Auto Hot Cue Labeler
"""

import sys
from pathlib import Path
from typing import Optional

import click
from tqdm import tqdm

# Windows consoles default to a legacy codepage (e.g. cp1252) that can't encode
# many filename characters (curly quotes, accents, emoji). Force UTF-8 so
# track names with those characters don't crash mid-run.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".aiff", ".aif", ".m4a", ".ogg"}


def _collect_audio(path: str):
    p = Path(path)
    if p.is_file():
        return [str(p)] if p.suffix.lower() in AUDIO_EXTENSIONS else []
    return sorted(
        str(f) for f in p.rglob("*")
        if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS
    )


def _fmt_time(sec: float) -> str:
    m, s = divmod(sec, 60)
    return f"{int(m)}:{s:05.2f}"


@click.group()
def cli():
    """Soundwave: auto-label your Rekordbox library with hot cues via XML import."""
    pass


@cli.command()
@click.argument("path")
@click.option("--output", "-o", default="soundwave_output.xml",
              help="Output XML path (default: soundwave_output.xml).")
@click.option("--base-xml", default=None,
              help="Your existing Rekordbox XML export to merge into (optional).")
@click.option("--dry-run", is_flag=True,
              help="Detect and print cues without writing any XML.")
@click.option("--no-lyrics", is_flag=True,
              help="Skip vocal detection (faster, skips Demucs model download).")
@click.option("--playlist", default=None, metavar="NAME",
              help="Add all analyzed tracks to a Rekordbox playlist with this name.")
@click.option("--cache", default=None, metavar="JSON_PATH",
              help="Analysis cache file (default: <path>/.soundwave_analysis_cache.json). "
                   "Re-running reuses cached results for unchanged files, so rebuilding the "
                   "XML after it's lost or overwritten is near-instant instead of re-analyzing.")
@click.option("--recompute", is_flag=True,
              help="Ignore the cache and re-analyze every file.")
def analyze(path: str, output: str, base_xml: Optional[str], dry_run: bool, no_lyrics: bool,
            playlist: Optional[str], cache: Optional[str], recompute: bool):
    """
    Analyze audio file(s) and generate a Rekordbox XML with hot cues.

    After running, import the XML into Rekordbox:
      File → Import Collection in xml format → select the output file
      Then right-click each track → Import.

    NOTE: If a track already exists in Rekordbox, you must delete it from the
    library first, then import the XML — otherwise Rekordbox won't update its cues.
    """
    from soundwave.analyzer import analyze_track_cached, load_analysis_cache, save_analysis_cache
    from soundwave.rekordbox.xml_handler import build_xml, load_tracks_from_xml, save_xml

    files = _collect_audio(path)
    if not files:
        click.echo(f"No audio files found at: {path}")
        sys.exit(1)

    root_path = Path(path)
    cache_path = Path(cache) if cache else (
        root_path / ".soundwave_analysis_cache.json" if root_path.is_dir()
        else root_path.parent / ".soundwave_analysis_cache.json"
    )
    analysis_cache = {} if recompute else load_analysis_cache(cache_path)

    click.echo(f"Found {len(files)} audio file(s).")
    if not no_lyrics:
        click.echo("Vocal detection enabled (Demucs). First run downloads ~80MB model.")
    click.echo(f"Using analysis cache: {cache_path}")

    analyses = []
    failed = []

    for audio_path in tqdm(files, desc="Analyzing"):
        name = Path(audio_path).name
        click.echo(f"\n{'='*60}")
        click.echo(f"Track: {name}")
        try:
            result, was_cached = analyze_track_cached(
                audio_path, analysis_cache, use_demucs=not no_lyrics,
            )
            analyses.append(result)
            if was_cached:
                click.echo("  (from cache)")
            _print_cues(result)
        except Exception as e:
            click.echo(f"  ERROR: {e}")
            failed.append(name)
        finally:
            # Save after every track, not just at the end, so an interrupted
            # or crashed run doesn't lose progress already computed.
            save_analysis_cache(cache_path, analysis_cache)

    if not analyses:
        click.echo("\nNo tracks successfully analyzed.")
        sys.exit(1)

    if dry_run:
        click.echo("\n[Dry run] No XML written.")
        return

    root = build_xml(analyses, base_xml_path=base_xml, playlist_name=playlist)
    save_xml(root, output)
    click.echo(f"\nXML written to: {Path(output).resolve()}")
    if playlist:
        click.echo(f"Playlist '{playlist}' contains {len(analyses)} track(s).")
    click.echo("\nNext steps:")
    click.echo("  1. Open Rekordbox")
    click.echo(f"  2. File → Import Collection in xml format → {output}")
    click.echo("  3. Right-click each track under the XML node → Import to Collection")
    click.echo("     (If the track already exists, delete it from your library first)")

    if failed:
        click.echo(f"\nFailed ({len(failed)}): {', '.join(failed)}")


@cli.command(name="show-xml")
@click.argument("xml_path")
def show_xml(xml_path: str):
    """Pretty-print tracks and cues from a Rekordbox XML file."""
    from soundwave.rekordbox.xml_handler import load_tracks_from_xml

    tracks = load_tracks_from_xml(xml_path)
    if not tracks:
        click.echo("No tracks found.")
        return
    for t in tracks:
        click.echo(f"\n{t['artist']} — {t['title']}  (BPM: {t['bpm']})")
        for cue in t["cues"]:
            slot = chr(ord("A") + cue["slot"]) if cue["slot"] >= 0 else "M"
            click.echo(f"  [{slot}] {cue['label']:<6}  {_fmt_time(cue['time_sec'])}")


@cli.command(name="playlist")
@click.argument("name")
@click.argument("path")
@click.option("--output", "-o", default="soundwave_output.xml",
              help="Output XML path (default: soundwave_output.xml).")
@click.option("--base-xml", default=None,
              help="Existing Rekordbox XML to merge into (optional).")
@click.option("--folder", default=None, metavar="FOLDER",
              help="Nest the playlist inside a Rekordbox folder with this name.")
def playlist_cmd(name: str, path: str, output: str, base_xml: Optional[str],
                 folder: Optional[str]):
    """
    Add audio files to a named Rekordbox playlist without re-analyzing.

    NAME  Playlist name as it will appear in Rekordbox.
    PATH  Audio file or directory to add.

    Tracks not yet in the XML collection are added with basic metadata only
    (no hot cues).  Run 'analyze' first if you want cues too.
    """
    from soundwave.rekordbox.xml_handler import (
        add_playlist, build_xml, load_tracks_from_xml, save_xml,
        upsert_tracks_minimal,
    )
    import xml.etree.ElementTree as ET

    files = _collect_audio(path)
    if not files:
        click.echo(f"No audio files found at: {path}")
        sys.exit(1)

    # Load or create the XML tree
    if base_xml and Path(base_xml).exists():
        root = ET.parse(base_xml).getroot()
    elif Path(output).exists():
        root = ET.parse(output).getroot()
    else:
        from soundwave.rekordbox.xml_handler import build_xml
        root = build_xml([])

    track_ids = upsert_tracks_minimal(root, files)
    add_playlist(root, name, track_ids, folder=folder)
    save_xml(root, output)

    loc = f"'{folder}' > '{name}'" if folder else f"'{name}'"
    click.echo(f"Playlist {loc} → {len(files)} track(s) written to {Path(output).resolve()}")


@cli.command(name="organize")
@click.argument("xml_path")
@click.option("--by", "methods",
              type=click.Choice(["bpm", "folder"]),
              multiple=True, default=["folder"],
              help="Organization method(s): bpm, folder (default: folder).")
@click.option("--bpm-step", default=10, show_default=True,
              help="Width of each BPM bucket (only used with --by bpm).")
@click.option("--output", "-o", default=None,
              help="Output XML path (default: overwrite xml_path).")
def organize(xml_path: str, methods, bpm_step: int, output: Optional[str]):
    """
    Auto-organize an existing XML collection into Rekordbox playlists.

    \b
    Methods:
      folder  One playlist per source directory (e.g. 'House', 'Techno')
      bpm     One playlist per BPM bucket (e.g. '120-130 BPM', '130-140 BPM')

    Both methods can be used together:
      python main.py organize library.xml --by folder --by bpm
    """
    import xml.etree.ElementTree as ET
    from soundwave.rekordbox.xml_handler import (
        organize_by_bpm, organize_by_folder, save_xml,
    )

    if not Path(xml_path).exists():
        click.echo(f"File not found: {xml_path}")
        sys.exit(1)

    root = ET.parse(xml_path).getroot()

    for method in methods:
        if method == "bpm":
            organize_by_bpm(root, step=bpm_step)
            click.echo(f"  Created BPM playlists (step={bpm_step}).")
        elif method == "folder":
            organize_by_folder(root)
            click.echo("  Created folder-based playlists.")

    dest = output or xml_path
    save_xml(root, dest)
    click.echo(f"Saved to {Path(dest).resolve()}")


@cli.command(name="tidy")
@click.argument("path")
@click.option("--fill-tags", is_flag=True,
              help="Fill in blank title / artist / genre tags from filename and folder name.")
@click.option("--fill-bpm", is_flag=True,
              help="Also detect and write missing BPM tags (slower, uses librosa).")
@click.option("--lookup", is_flag=True,
              help="Fingerprint each file and pull metadata + album art from AcoustID / "
                   "MusicBrainz / Cover Art Archive (all free). Requires --acoustid-key and fpcalc.")
@click.option("--acoustid-key", default=None, envvar="ACOUSTID_KEY",
              metavar="KEY",
              help="AcoustID API key (free at https://acoustid.org/login). "
                   "Can also be set via the ACOUSTID_KEY environment variable.")
@click.option("--no-art", is_flag=True,
              help="Skip album art download when using --lookup.")
@click.option("--overwrite", is_flag=True,
              help="Overwrite existing tags when using --lookup (default: only fill blanks).")
@click.option("--sort-genres", is_flag=True,
              help="Move files into sub-folders named after their genre tag.")
@click.option("--dedupe", is_flag=True,
              help="Delete duplicate files that share the same title and artist, keeping the largest.")
@click.option("--rename", is_flag=True,
              help="Rename each file to its track title (sanitized).")
@click.option("--clear-genre", multiple=True, metavar="FOLDER",
              help="Clear the genre tag for files inside FOLDER, but only if that tag "
                   "just echoes the folder's own name (can be repeated). Use this to undo "
                   "corruption from an earlier --fill-tags run on non-genre folders, e.g. "
                   "--clear-genre Touse --clear-genre \"Pool Party\"")
@click.option("--ignore", multiple=True, metavar="FOLDER",
              help="Folder name to skip (can be repeated). Case-insensitive. "
                   "Example: --ignore rekordbox --ignore iTunes")
@click.option("--dry-run", is_flag=True,
              help="Preview all changes without touching the filesystem.")
def tidy(path: str, fill_tags: bool, fill_bpm: bool,
         lookup: bool, acoustid_key: Optional[str], no_art: bool, overwrite: bool,
         sort_genres: bool, dedupe: bool, rename: bool, clear_genre: tuple,
         ignore: tuple, dry_run: bool):
    """
    Tidy a music folder: fill tags, look up metadata, sort by genre, dedupe, rename.

    \b
    Run order (when multiple flags given):
      1. --clear-genre reset genre tags that just echo their own (non-genre) folder name
      2. --fill-tags   write missing title / artist / genre / BPM from filename/folder
      3. --lookup      fingerprint files -> AcoustID -> MusicBrainz + Cover Art Archive
      4. --dedupe      delete duplicate tracks (same title), keep the largest file
      5. --sort-genres move files into <genre>/ sub-folders
      6. --rename      rename each file to its track title

    \b
    One-time setup for --lookup:
      1. Register for a free API key at https://acoustid.org/login
      2. Download fpcalc from https://acoustid.org/chromaprint and put it on PATH
      3. pip install pyacoustid requests

    Use --dry-run to preview every change before committing.
    """
    from soundwave.library import (
        clear_genre_from_folders, dedupe_by_title, enrich_with_lookup,
        fill_missing_tags, rename_to_track_name, sort_by_genre,
    )

    root = Path(path)
    if not root.is_dir():
        click.echo(f"Not a directory: {path}")
        sys.exit(1)

    if not any([fill_tags, lookup, sort_genres, dedupe, rename, clear_genre]):
        click.echo("Nothing to do. Pass at least one of: "
                   "--clear-genre, --fill-tags, --lookup, --sort-genres, --dedupe, --rename")
        sys.exit(0)

    if lookup and not acoustid_key:
        click.echo(
            "ERROR: --lookup requires an AcoustID API key.\n"
            "  Get one free at: https://acoustid.org/login\n"
            "  Then pass it with --acoustid-key KEY  or  set ACOUSTID_KEY=KEY"
        )
        sys.exit(1)

    ignore_set = set(ignore) if ignore else None

    if dry_run:
        click.echo("[Dry run] No files will be modified.\n")
    if ignore_set:
        click.echo(f"Ignoring folders: {', '.join(sorted(ignore_set))}\n")

    def _stream(label: str, gen):
        """Print a section header then each line as it arrives."""
        click.echo(f"--- {label} ---")
        count = 0
        for line in gen:
            click.echo(f"  {line}")
            count += 1
        if count == 0:
            click.echo("  (nothing to do)")
        click.echo()

    if clear_genre:
        _stream("Clearing bogus genre tags",
                clear_genre_from_folders(root, set(clear_genre), ignore=ignore_set, dry_run=dry_run))

    if fill_tags:
        _stream("Filling missing tags",
                fill_missing_tags(root, fill_bpm=fill_bpm, ignore=ignore_set, dry_run=dry_run))

    if lookup:
        click.echo("--- Fingerprint lookup (AcoustID -> MusicBrainz -> Cover Art) ---")
        click.echo("  MusicBrainz is rate-limited to 1 req/sec — results stream as each file completes.")
        click.echo()
        count = 0
        for line in enrich_with_lookup(
            root, acoustid_key,
            overwrite=overwrite,
            fetch_art=not no_art,
            ignore=ignore_set,
            dry_run=dry_run,
        ):
            click.echo(f"  {line}")
            count += 1
        if count == 0:
            click.echo("  (no files processed)")
        click.echo()

    if dedupe:
        _stream("Deduplicating by title",
                dedupe_by_title(root, ignore=ignore_set, dry_run=dry_run))

    if sort_genres:
        _stream("Sorting into genre folders",
                sort_by_genre(root, ignore=ignore_set, dry_run=dry_run))

    if rename:
        _stream("Renaming files to track titles",
                rename_to_track_name(root, ignore=ignore_set, dry_run=dry_run))

    if dry_run:
        click.echo("[Dry run complete] Re-run without --dry-run to apply changes.")
    else:
        click.echo("Done.")


@cli.command(name="similarity-playlists")
@click.argument("path")
@click.option("--output", "-o", default="soundwave_output.xml",
              help="Output XML path (default: soundwave_output.xml).")
@click.option("--base-xml", default=None,
              help="Existing Rekordbox XML to merge into (optional).")
@click.option("--mode", type=click.Choice(["clusters", "path", "both"]), default="both",
              show_default=True,
              help="clusters = group similar-sounding tracks into N playlists; "
                   "path = one playlist ordered for smooth track-to-track flow; "
                   "both = do both.")
@click.option("--clusters", default=6, show_default=True,
              help="Number of similarity clusters to build (only used with --mode clusters/both).")
@click.option("--folder", default="Similarity", show_default=True, metavar="FOLDER",
              help="Rekordbox folder name to nest the generated playlists inside.")
@click.option("--analysis-sec", default=60.0, show_default=True,
              help="Seconds of audio analyzed per track, taken from the middle (lower = faster).")
@click.option("--cache", default=None, metavar="JSON_PATH",
              help="Feature cache file (default: <path>/.soundwave_features.json). "
                   "Re-runs reuse cached features for unchanged files.")
@click.option("--recompute", is_flag=True,
              help="Ignore the cache and re-extract features for every track.")
def similarity_playlists(path: str, output: str, base_xml: Optional[str], mode: str,
                          clusters: int, folder: str, analysis_sec: float,
                          cache: Optional[str], recompute: bool):
    """
    Build Rekordbox playlists that group tracks by audio similarity
    (tempo, key/chroma, timbre, energy) instead of folder or genre tags.

    \b
    Non-destructive: nothing on disk is moved or renamed. Everything happens
    inside the output XML, which you import into Rekordbox the same way as
    'analyze' output (File -> Import Collection in xml format).

    \b
    Modes:
      clusters  N playlists, each a group of similar-sounding tracks.
      path      One playlist per run, ordered so each track flows into the
                next (nearest-neighbor route through the feature space) —
                use this as a ready-made DJ set order.
      both      Build both (default).
    """
    import xml.etree.ElementTree as ET

    from soundwave.rekordbox.xml_handler import (
        add_playlist, build_xml, remove_playlists_with_prefix, save_xml, upsert_tracks_minimal,
    )
    from soundwave.similarity import (
        cluster_summary, cluster_tracks, extract_features, similarity_path_order,
    )

    root_path = Path(path)
    files = _collect_audio(path)
    if not files:
        click.echo(f"No audio files found at: {path}")
        sys.exit(1)

    cache_path = Path(cache) if cache else (
        root_path / ".soundwave_features.json" if root_path.is_dir()
        else root_path.parent / ".soundwave_features.json"
    )

    click.echo(f"Found {len(files)} audio file(s). Extracting features "
               f"({analysis_sec:.0f}s/track, cached at {cache_path})...")
    features = extract_features(
        files, cache_path, analysis_sec=analysis_sec, recompute=recompute,
        progress=lambda it: tqdm(it, desc="Analyzing"),
    )

    failed = len(files) - len(features)
    if failed:
        click.echo(f"  {failed} file(s) failed feature extraction and will be skipped.")
    if len(features) < 2:
        click.echo("Not enough analyzable tracks to build playlists.")
        sys.exit(1)

    # Load or create the XML tree
    if base_xml and Path(base_xml).exists():
        xml_root = ET.parse(base_xml).getroot()
    elif Path(output).exists():
        xml_root = ET.parse(output).getroot()
    else:
        xml_root = build_xml([])

    analyzed_paths = list(features.keys())
    track_ids = upsert_tracks_minimal(xml_root, analyzed_paths)
    id_by_path = dict(zip(analyzed_paths, track_ids))

    if mode in ("clusters", "both"):
        remove_playlists_with_prefix(xml_root, folder, "Similarity ")
        groups = cluster_tracks(features, clusters)
        click.echo(f"\nBuilt {len(groups)} similarity cluster(s):")
        for i, group_paths in groups.items():
            name = f"Similarity {i + 1} ({cluster_summary(features, group_paths)})"
            ids = [id_by_path[p] for p in group_paths]
            add_playlist(xml_root, name, ids, folder=folder)
            click.echo(f"  '{name}' — {len(ids)} track(s)")

    if mode in ("path", "both"):
        ordered_paths = similarity_path_order(features)
        ids = [id_by_path[p] for p in ordered_paths]
        add_playlist(xml_root, "DJ Set Path", ids, folder=folder)
        click.echo(f"\nBuilt ordered path playlist 'DJ Set Path' — {len(ids)} track(s).")

    save_xml(xml_root, output)
    click.echo(f"\nXML written to: {Path(output).resolve()}")
    click.echo("Next: Rekordbox -> File -> Import Collection in xml format -> select the file above.")


@cli.command(name="show-playlists")
@click.argument("xml_path")
def show_playlists(xml_path: str):
    """Show the playlist tree inside a Rekordbox XML file."""
    from soundwave.rekordbox.xml_handler import load_playlists_from_xml

    nodes = load_playlists_from_xml(xml_path)
    if not nodes:
        click.echo("No playlists found.")
        return
    _print_playlist_tree(nodes)


def _print_playlist_tree(nodes: list, indent: int = 0) -> None:
    prefix = "  " * indent
    for node in nodes:
        if node["type"] == "folder":
            click.echo(f"{prefix}[folder] {node['name']}/")
            _print_playlist_tree(node["children"], indent + 1)
        else:
            tracks = node["tracks"]
            click.echo(f"{prefix}[playlist] {node['name']}  ({len(tracks)} tracks)")
            for t in tracks:
                artist = t.get("artist", "")
                title = t.get("title", "")
                bpm = t.get("bpm", "")
                label = f"{artist} — {title}" if artist else title
                click.echo(f"{prefix}  • {label}  {bpm}")


def _print_cues(result) -> None:
    if not result.cues:
        click.echo("  (no cues detected)")
        return
    for i, cue in enumerate(result.cues):
        slot = chr(ord("A") + i)
        click.echo(f"  [{slot}] {cue.label:<6}  {_fmt_time(cue.time_sec)}")


if __name__ == "__main__":
    cli()
