"""
Unit tests for the Rekordbox XML handler.

Tests XML generation, parsing, URI conversion, and safe merging.
No Rekordbox installation required.
"""

import xml.etree.ElementTree as ET
from pathlib import Path
import tempfile
import os
import pytest

from soundwave.rekordbox.xml_handler import (
    build_xml,
    load_tracks_from_xml,
    path_to_uri,
    save_xml,
    uri_to_path,
    _split_rgb,
)
from soundwave.rekordbox.models import CuePoint, TrackAnalysis


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_analysis(
    path: str = "C:/Music/test.mp3",
    title: str = "Test Track",
    artist: str = "Test Artist",
    cues=None,
) -> TrackAnalysis:
    return TrackAnalysis(
        audio_path=path,
        title=title,
        artist=artist,
        duration_sec=180.0,
        bpm=128.0,
        cues=cues or [
            CuePoint("INTRO", 8.0, 0xFFFFFF),
            CuePoint("DROP", 32.0, 0xFF0000),
            CuePoint("BUILD", 16.0, 0x00FF00),
        ],
    )


# ---------------------------------------------------------------------------
# URI conversion
# ---------------------------------------------------------------------------

class TestUriConversion:
    def test_windows_path_to_uri(self):
        uri = path_to_uri("C:/Music/my track.mp3")
        assert uri.startswith("file://localhost/C:/")
        assert "my%20track.mp3" in uri or "my track.mp3" in uri

    def test_uri_to_windows_path(self):
        uri = "file://localhost/C:/Music/foo.mp3"
        result = uri_to_path(uri)
        assert "C:" in result
        assert "foo.mp3" in result

    def test_uri_to_windows_path_legacy_format(self):
        """Old Soundwave output (or other tools) may use plain file:/// —
        still needs to parse correctly."""
        uri = "file:///C:/Music/foo.mp3"
        result = uri_to_path(uri)
        assert "C:" in result
        assert "foo.mp3" in result

    def test_roundtrip(self):
        original = "C:/Music/Artist - Track (Original Mix).mp3"
        roundtrip = uri_to_path(path_to_uri(original))
        # Normalize separators for comparison
        assert roundtrip.replace("\\", "/") == original

    def test_spaces_are_encoded(self):
        uri = path_to_uri("C:/My Music/track name.mp3")
        assert " " not in uri


# ---------------------------------------------------------------------------
# Color splitting
# ---------------------------------------------------------------------------

class TestColorSplit:
    def test_red(self):
        assert _split_rgb(0xFF0000) == (255, 0, 0)

    def test_blue(self):
        assert _split_rgb(0x0000FF) == (0, 0, 255)

    def test_green(self):
        assert _split_rgb(0x00FF00) == (0, 255, 0)

    def test_white(self):
        assert _split_rgb(0xFFFFFF) == (255, 255, 255)

    def test_yellow(self):
        assert _split_rgb(0xFFFF00) == (255, 255, 0)


# ---------------------------------------------------------------------------
# XML generation
# ---------------------------------------------------------------------------

class TestBuildXml:
    def test_creates_dj_playlists_root(self):
        root = build_xml([_make_analysis()])
        assert root.tag == "DJ_PLAYLISTS"

    def test_creates_collection_element(self):
        root = build_xml([_make_analysis()])
        assert root.find("COLLECTION") is not None

    def test_track_count_in_collection(self):
        analyses = [_make_analysis(path=f"C:/Music/track{i}.mp3") for i in range(3)]
        root = build_xml(analyses)
        collection = root.find("COLLECTION")
        tracks = collection.findall("TRACK")
        assert len(tracks) == 3

    def test_position_marks_created(self):
        analysis = _make_analysis()
        root = build_xml([analysis])
        collection = root.find("COLLECTION")
        track = collection.find("TRACK")
        marks = track.findall("POSITION_MARK")
        assert len(marks) == len(analysis.cues)

    def test_cue_positions_are_correct(self):
        cues = [CuePoint("DROP", 32.5, 0xFF0000)]
        root = build_xml([_make_analysis(cues=cues)])
        mark = root.find(".//POSITION_MARK")
        assert float(mark.get("Start")) == pytest.approx(32.5, abs=0.001)

    def test_cue_labels_written(self):
        cues = [
            CuePoint("DROP", 32.0, 0xFF0000),
            CuePoint("INTRO", 8.0, 0xFFFFFF),
        ]
        root = build_xml([_make_analysis(cues=cues)])
        names = {m.get("Name") for m in root.findall(".//POSITION_MARK")}
        assert "DROP" in names
        assert "INTRO" in names

    def test_cue_slot_numbers_sequential(self):
        cues = [CuePoint("DROP", float(i * 10), 0xFF0000) for i in range(4)]
        root = build_xml([_make_analysis(cues=cues)])
        nums = [int(m.get("Num")) for m in root.findall(".//POSITION_MARK")]
        assert nums == [0, 1, 2, 3]

    def test_hot_cue_type_is_zero(self):
        root = build_xml([_make_analysis()])
        for mark in root.findall(".//POSITION_MARK"):
            assert mark.get("Type") == "0"

    def test_rgb_split_in_xml(self):
        cues = [CuePoint("DROP", 10.0, 0xFF0000)]  # pure red
        root = build_xml([_make_analysis(cues=cues)])
        mark = root.find(".//POSITION_MARK")
        assert mark.get("Red") == "255"
        assert mark.get("Green") == "0"
        assert mark.get("Blue") == "0"

    def test_generator_marker_set(self):
        root = build_xml([_make_analysis()])
        for mark in root.findall(".//POSITION_MARK"):
            assert mark.get("Generator") == "soundwave"

    def test_multiple_tracks_get_unique_ids(self):
        analyses = [_make_analysis(path=f"C:/Music/track{i}.mp3") for i in range(3)]
        root = build_xml(analyses)
        ids = [el.get("TrackID") for el in root.findall(".//TRACK")]
        assert len(set(ids)) == 3


# ---------------------------------------------------------------------------
# Save and load roundtrip
# ---------------------------------------------------------------------------

class TestXmlRoundtrip:
    def test_save_creates_file(self, tmp_path):
        root = build_xml([_make_analysis()])
        out = str(tmp_path / "output.xml")
        save_xml(root, out)
        assert Path(out).exists()

    def test_saved_file_is_valid_xml(self, tmp_path):
        root = build_xml([_make_analysis()])
        out = str(tmp_path / "output.xml")
        save_xml(root, out)
        # Should not raise
        ET.parse(out)

    def test_saved_file_has_utf8_declaration(self, tmp_path):
        root = build_xml([_make_analysis()])
        out = str(tmp_path / "output.xml")
        save_xml(root, out)
        content = Path(out).read_text(encoding="utf-8")
        assert 'encoding="UTF-8"' in content

    def test_load_tracks_reads_saved_xml(self, tmp_path):
        cues = [CuePoint("DROP", 32.0, 0xFF0000), CuePoint("INTRO", 8.0, 0xFFFFFF)]
        root = build_xml([_make_analysis(title="My Track", cues=cues)])
        out = str(tmp_path / "output.xml")
        save_xml(root, out)

        tracks = load_tracks_from_xml(out)
        assert len(tracks) == 1
        assert tracks[0]["title"] == "My Track"
        assert len(tracks[0]["cues"]) == 2

    def test_cue_position_preserved_through_roundtrip(self, tmp_path):
        cues = [CuePoint("DROP", 45.678, 0xFF0000)]
        root = build_xml([_make_analysis(cues=cues)])
        out = str(tmp_path / "output.xml")
        save_xml(root, out)

        tracks = load_tracks_from_xml(out)
        assert tracks[0]["cues"][0]["time_sec"] == pytest.approx(45.678, abs=0.001)


# ---------------------------------------------------------------------------
# Merging with existing XML (the safe-update path)
# ---------------------------------------------------------------------------

class TestMergeWithExisting:
    def _write_base_xml(self, tmp_path) -> str:
        """Write a minimal base XML with one track and one manual cue."""
        xml_content = '''<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.0.0" Company="AlphaTheta"/>
  <COLLECTION Entries="1">
    <TRACK TrackID="1" Name="Existing Track" Artist="DJ" Location="file:///C:/Music/existing.mp3"
           TotalTime="180" AverageBpm="128.00">
      <POSITION_MARK Name="manual_cue" Type="0" Start="10.0" Num="0" Red="255" Green="255" Blue="0"/>
    </TRACK>
  </COLLECTION>
  <PLAYLISTS><NODE Type="0" Name="ROOT" Count="0"/></PLAYLISTS>
</DJ_PLAYLISTS>'''
        p = tmp_path / "base.xml"
        p.write_text(xml_content, encoding="utf-8")
        return str(p)

    def test_new_track_added_to_existing_xml(self, tmp_path):
        base = self._write_base_xml(tmp_path)
        new_analysis = _make_analysis(path="C:/Music/new_track.mp3", title="New Track")
        root = build_xml([new_analysis], base_xml_path=base)
        tracks = root.findall(".//TRACK")
        titles = {t.get("Name") for t in tracks}
        assert "Existing Track" in titles
        assert "New Track" in titles

    def test_manual_cues_preserved_on_existing_track(self, tmp_path):
        base = self._write_base_xml(tmp_path)
        # Re-analyze the existing track
        analysis = _make_analysis(
            path="C:/Music/existing.mp3",
            cues=[CuePoint("DROP", 32.0, 0xFF0000)],
        )
        root = build_xml([analysis], base_xml_path=base)
        marks = root.findall(".//POSITION_MARK")
        names = {m.get("Name") for m in marks}
        # Manual cue (not Generator=soundwave) should be preserved
        assert "manual_cue" in names
        assert "DROP" in names

    def test_soundwave_cues_replaced_on_rerun(self, tmp_path):
        """Running twice should not double-add soundwave cues."""
        analysis = _make_analysis(cues=[CuePoint("DROP", 32.0, 0xFF0000)])
        root1 = build_xml([analysis])
        out = str(tmp_path / "pass1.xml")
        save_xml(root1, out)

        root2 = build_xml([analysis], base_xml_path=out)
        out2 = str(tmp_path / "pass2.xml")
        save_xml(root2, out2)

        tracks = load_tracks_from_xml(out2)
        soundwave_cues = [c for c in tracks[0]["cues"] if True]  # all are soundwave
        # Should still have exactly 1 DROP, not 2
        drop_count = sum(1 for c in tracks[0]["cues"] if c["label"] == "DROP")
        assert drop_count == 1
