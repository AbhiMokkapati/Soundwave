"""
Regression tests for bugs found in the correctness audit. Each one fails on
the pre-fix code. Tag reads are mocked so no real audio files are needed.
"""

import asyncio
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

import pytest

from soundwave import library as lib
from soundwave import similarity as sim
from soundwave.rekordbox.xml_handler import (
    add_playlist, build_xml, remove_playlists_with_prefix, save_xml,
)


def _tags(**fields):
    return {k: [v] for k, v in fields.items()}


# ---------------------------------------------------------------------------
# library.py
# ---------------------------------------------------------------------------

class TestDedupe:
    def test_same_title_different_artist_is_not_a_duplicate(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        (tmp_path / "a" / "1.mp3").write_bytes(b"0" * 100)
        (tmp_path / "b" / "2.mp3").write_bytes(b"0" * 50)

        def tags_for(path):
            artist = "Chainsmokers" if path.parent.name == "a" else "Nine Inch Nails"
            return _tags(title="Closer", artist=artist)

        with mock.patch.object(lib, "_read_easy_tags", side_effect=tags_for):
            lines = list(lib.dedupe_by_title(tmp_path, dry_run=True))
        assert not any(line.startswith("DELETE") for line in lines)

    def test_same_title_same_artist_is_still_deduped(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        (tmp_path / "a" / "1.mp3").write_bytes(b"0" * 100)
        (tmp_path / "b" / "2.mp3").write_bytes(b"0" * 50)

        with mock.patch.object(lib, "_read_easy_tags", return_value=_tags(title="Closer", artist="X")):
            lines = list(lib.dedupe_by_title(tmp_path, dry_run=True))
        deletes = [line for line in lines if line.startswith("DELETE")]
        assert len(deletes) == 1 and "2.mp3" in deletes[0]


class TestRenameKeepsRealTitles:
    @pytest.mark.parametrize("title", [
        "Levitating - Remix", "Studio 54", "Blink 182", "Hey Jude - Remastered 2015",
    ])
    def test_good_title_tag_is_not_mangled(self, tmp_path, title):
        (tmp_path / "x.mp3").write_bytes(b"0")
        with mock.patch.object(lib, "_read_easy_tags", return_value=_tags(title=title)):
            lines = list(lib.rename_to_track_name(tmp_path, dry_run=True))
        assert lines == [f"RENAME 'x.mp3' -> '{lib._sanitize(title)}.mp3'"]

    def test_bracketed_download_noise_is_still_stripped(self, tmp_path):
        (tmp_path / "x.mp3").write_bytes(b"0")
        with mock.patch.object(lib, "_read_easy_tags", return_value=_tags(title="Havana (Official Audio)")):
            lines = list(lib.rename_to_track_name(tmp_path, dry_run=True))
        assert lines == ["RENAME 'x.mp3' -> 'Havana.mp3'"]

    def test_filename_derived_titles_still_lose_trailing_counter(self):
        assert lib._clean_title_from_stem("Song Name 2") == "Song Name"


# ---------------------------------------------------------------------------
# similarity.py
# ---------------------------------------------------------------------------

def test_similarity_cache_is_keyed_by_analysis_window(tmp_path):
    f = tmp_path / "t.mp3"
    f.write_bytes(b"0")
    cache = tmp_path / "c.json"
    seen = []

    def fake_extract(path, analysis_sec):
        seen.append(analysis_sec)
        return {k: float(analysis_sec) for k in sim._FEATURE_KEYS}

    with mock.patch.object(sim, "_extract_raw_features", side_effect=fake_extract):
        first = sim.extract_features([str(f)], cache, analysis_sec=60)
        second = sim.extract_features([str(f)], cache, analysis_sec=10)
        third = sim.extract_features([str(f)], cache, analysis_sec=10)

    assert seen == [60, 10], "changing analysis_sec must re-extract; an unchanged one must not"
    assert next(iter(first.values()))["tempo"] == 60.0
    assert next(iter(second.values()))["tempo"] == 10.0
    assert third == second


# ---------------------------------------------------------------------------
# xml_handler.py: stale generated playlists
# ---------------------------------------------------------------------------

def test_remove_playlists_with_prefix_clears_previous_run():
    root = build_xml([])
    add_playlist(root, "Similarity 1 (120 BPM, high-energy)", [1], folder="Similarity")
    add_playlist(root, "Similarity 4 (90 BPM, low-energy)", [2], folder="Similarity")
    add_playlist(root, "DJ Set Path", [1, 2], folder="Similarity")

    removed = remove_playlists_with_prefix(root, "Similarity", "Similarity ")

    names = [n.get("Name") for n in root.find(".//NODE[@Name='Similarity']")]
    assert removed == 2
    assert names == ["DJ Set Path"]
    assert root.find(".//NODE[@Name='Similarity']").get("Count") == "1"


def test_remove_playlists_with_prefix_missing_folder_is_noop():
    assert remove_playlists_with_prefix(build_xml([]), "Similarity", "Similarity ") == 0


# ---------------------------------------------------------------------------
# webapp/server.py
# ---------------------------------------------------------------------------

@pytest.fixture
def server():
    root = str(Path(__file__).resolve().parent.parent)
    sys.path.insert(0, root)
    sys.path.insert(0, str(Path(root) / "webapp"))
    try:
        import server as mod
    finally:
        sys.path.remove(str(Path(root) / "webapp"))
    return mod


def _drain(response):
    async def run():
        return "".join([c if isinstance(c, str) else c.decode() async for c in response.body_iterator])
    return asyncio.run(run())


class TestBatchAnalyzeWithBaseXml:
    def test_every_track_survives_when_base_xml_is_given(self, server, tmp_path):
        library = tmp_path / "lib"
        library.mkdir()
        for name in ("t1", "t2", "t3"):
            (library / f"{name}.mp3").write_bytes(b"0")
        base = tmp_path / "base.xml"
        save_xml(build_xml([]), str(base))
        out = tmp_path / "out.xml"

        def fake_analyze(p, use_demucs, recompute, cache):
            return {"title": p.stem, "artist": "A", "bpm": 120.0, "duration_sec": 100.0,
                    "cues": [{"label": "DROP", "time_sec": 10.0, "color": 1}]}

        with mock.patch.object(server, "_analyze_one", side_effect=fake_analyze), \
             mock.patch.object(server, "_analysis_cache", return_value={}), \
             mock.patch.object(server, "_save_analysis_cache"), \
             mock.patch.object(server, "_notify_windows"):
            _drain(server.analyze_stream(path=str(library), output=str(out), base_xml=str(base),
                                         use_demucs=False, recompute=False))

        names = [t.get("Name") for t in ET.parse(out).getroot().find("COLLECTION")]
        assert names == ["t1", "t2", "t3"]


class TestAudioEndpoint:
    def test_refuses_non_audio_files(self, server, tmp_path):
        secret = tmp_path / "config.json"
        secret.write_text('{"acoustid_key": "x"}')
        assert server.track_audio(str(secret)).status_code == 400

    def test_serves_audio_files(self, server, tmp_path):
        song = tmp_path / "a.mp3"
        song.write_bytes(b"0")
        assert server.track_audio(str(song)).media_type == "audio/mpeg"


class TestLocalOnlyMiddleware:
    def _call(self, server, headers):
        from starlette.requests import Request
        scope = {
            "type": "http", "method": "GET", "path": "/", "query_string": b"",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        }

        async def call_next(_request):
            return "PASSED"

        return asyncio.run(server._local_only_guard(Request(scope), call_next))

    def test_local_same_origin_request_passes(self, server):
        assert self._call(server, {"host": "127.0.0.1:8765", "sec-fetch-site": "same-origin"}) == "PASSED"

    def test_direct_navigation_passes(self, server):
        assert self._call(server, {"host": "localhost:8765", "sec-fetch-site": "none"}) == "PASSED"

    def test_non_browser_client_passes(self, server):
        assert self._call(server, {"host": "127.0.0.1:8765"}) == "PASSED"

    def test_dns_rebinding_host_is_rejected(self, server):
        assert self._call(server, {"host": "evil.example:8765"}).status_code == 403

    def test_cross_site_request_is_rejected(self, server):
        resp = self._call(server, {"host": "127.0.0.1:8765", "sec-fetch-site": "cross-site"})
        assert resp.status_code == 403

    def test_foreign_origin_is_rejected(self, server):
        resp = self._call(server, {"host": "127.0.0.1:8765", "origin": "https://evil.example"})
        assert resp.status_code == 403
