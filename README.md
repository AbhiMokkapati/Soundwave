# Soundwave

Automatically detects drops, builds, lyric sections, and breakdowns in your music library and writes them as hot cues into Rekordbox — saving hours of manual cue-setting before a mix.

---

## How it works

Soundwave analyzes your audio files, detects musical landmarks, and generates a **Rekordbox XML file** you import directly into Rekordbox. No database manipulation, no risk of corrupting your library.

```
Your music files
      │
      ▼
  Soundwave analyzes each track:
  ┌────────────────────────────────────────┐
  │  librosa   → drops, builds, breakdowns │
  │  Demucs    → vocal/lyric sections      │
  └────────────────────────────────────────┘
      │
      ▼
  soundwave_output.xml
      │
      ▼
  Rekordbox (File → Import Collection in xml format)
      │
      ▼
  Hot cues appear on your tracks ✓
```

### Why XML instead of editing the database directly?

Rekordbox's SQLite database (`master.db`) is encrypted with SQLCipher and its schema changes between versions. Writing directly to it is brittle and can corrupt your library. The XML import path is:

- **Officially supported** by Pioneer/AlphaTheta across Rekordbox 5, 6, and 7
- **Non-destructive** — you import a file; nothing is overwritten until you choose to
- **Reversible** — if you don't like the result, just don't import it

---

## Hot cue labels and colors

| Label     | Color   | What it marks |
|-----------|---------|---------------|
| INTRO     | White   | Track start / mix-in point — the first bar line once the audio begins (skips silent lead-in) |
| DROP      | Red     | Main drop(s) — where the kick returns after a break and the mix gets louder (needs Demucs; otherwise the loudest sustained sections) — up to 4 per track |
| BUILD     | Green   | Start of the pre-drop build — where the high-frequency riser starts climbing, rounded to whole 4-bar phrases before the drop |
| BREAK     | Yellow  | Breakdown — the first beat where the kick leaves, for a section the kick later returns from |
| VOCAL     | Blue    | Sustained vocal entry (verse/chorus start) — up to 3 per track |
| OUTRO32   | Orange  | Start of the last 32 bars |
| OUTRO16   | Pink    | Start of the last 16 bars |
| SECTION   | Purple  | Generic structural change (e.g. a beat switch or verse→chorus) caught by spectral-novelty detection, for tracks without a clean energy-based drop — up to 3 per track |

Up to 8 hot cues are placed per track (Rekordbox's limit). When more are detected than fit: INTRO is always kept, then remaining slots are filled round-robin across DROP/SECTION/BUILD/VOCAL/BREAK — strongest first within each type — so one prolific type (e.g. 4 drops) can't crowd out the track's only build or vocal entry. OUTRO16/OUTRO32 are structural rather than analyzed, so they only fill genuinely leftover slots. See `_select_by_importance` in [`soundwave/analyzer.py`](soundwave/analyzer.py).

---

## Setup

### Requirements

- Python 3.10 or later — [python.org/downloads](https://python.org/downloads)
- ~2 GB disk space for PyTorch + Demucs model (downloaded on first use)
- Windows 10/11

### Install

Double-click **`setup.bat`** — it will:
1. Set the PowerShell execution policy for your user account
2. Create a `venv/` virtual environment
3. Install all dependencies

Or manually:

```bat
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

---

## Web app (recommended)

Double-click [`run_app.bat`](run_app.bat) — it starts a local server and opens `http://127.0.0.1:8765` in your browser. No flags to remember:

- **Library** — browse a folder, see title/artist/genre/BPM at a glance, and run tidy operations (clear-genre, fill-tags, AcoustID lookup, dedupe, sort-genres, rename) with checkboxes and a live log.
- **Analyze & Cues** — play a track against its waveform with a moving playhead, click any cue to jump there and audition it, drag to correct its position, add brand-new cues at the playhead, delete wrong ones, and save to a Rekordbox XML. Synced lyrics (via LRCLIB) scroll alongside, highlighting the current line.
- **Similarity Playlists** — the same clustering / DJ-set-path feature as the CLI below, with a progress bar.
- **Playlists** — inspect the tree inside any generated XML.

Everything below this point is the underlying CLI the web app calls into — useful for scripting or batch runs, but not required for day-to-day use.

---

## CLI usage

### Activate the environment first

```bat
venv\Scripts\activate
```

### Dry run — preview cues without writing anything

```bat
python main.py analyze "C:\Music\MyTrack.mp3" --dry-run
```

```bat
python main.py analyze "C:\Music" --dry-run --no-lyrics
```

### Analyze and generate XML

```bat
python main.py analyze "C:\Music" -o my_cues.xml
```

### Merge cues into your existing Rekordbox XML export

If you've already exported your library from Rekordbox (recommended):

```bat
python main.py analyze "C:\Music" --base-xml "C:\Users\you\Documents\rekordbox.xml" -o updated.xml
```

This preserves all your existing manual cues and only adds/replaces Soundwave-generated ones.

### Skip vocal detection (faster, no Demucs)

```bat
python main.py analyze "C:\Music" --no-lyrics -o my_cues.xml
```

### Inspect a generated XML file

```bat
python main.py show-xml my_cues.xml
```

### Build playlists by audio similarity (not folder/genre)

Groups tracks by how they actually sound (tempo, key, timbre, energy) and/or
orders them into one smooth-flowing set, and writes the result as Rekordbox
playlists. Nothing on disk is moved — this only touches the output XML.

```bat
python main.py similarity-playlists "C:\Music" --clusters 6 --mode both -o similarity.xml
```

- `--mode clusters` — N playlists of similar-sounding tracks (default 6, set with `--clusters`)
- `--mode path` — one playlist ordered so each track flows into the next (a ready-made DJ set order)
- `--mode both` — build both (default)
- `--folder NAME` — Rekordbox folder to nest the generated playlists under (default `Similarity`)
- `--analysis-sec N` — seconds analyzed per track from the middle of the file (default 60; lower = faster)
- Features are cached per-file in `<path>\.soundwave_features.json`, so re-runs after the first one are near-instant unless files change.

### Repair genre tags corrupted by a bad --fill-tags run

If `tidy --fill-tags` was run on a library where some folders are personal
buckets rather than real genres (e.g. "Party Mix", "Touse"), it will have
written that folder's own name into each file's genre tag — which then makes
`--sort-genres` think those files are already correctly filed, so they never
move. Fix it with:

```bat
python main.py tidy "C:\Music" --clear-genre "Party Mix" --clear-genre "Touse" --dry-run
```

This only clears a file's genre tag when it exactly matches its own parent
folder's name, and only for folders you name explicitly — real genre folders
are left untouched. Drop `--dry-run` to apply, then re-tag properly with
`--lookup` (see below) before running `--sort-genres`.

---

## Importing into Rekordbox

### Step-by-step

1. Open Rekordbox
2. **File → Import Collection in xml format** → select your XML file
3. The XML appears as a source in the left sidebar
4. Right-click a track under the XML source → **Import to Collection**

### Critical: existing tracks

> **Rekordbox will NOT update hot cues on a track that already exists in your library.**
> This is a known Rekordbox limitation that affects all versions from 5.6.1 onward.

**Workaround:**
1. In Rekordbox, right-click the track → **Remove from Collection** (this does NOT delete the file)
2. Import the XML — the track re-enters the library with the new hot cues
3. Re-add to any playlists as needed

For new tracks not yet in your library, import works with no extra steps.

---

## Tuning detection

All thresholds are in [`soundwave/config.py`](soundwave/config.py). The most useful ones:

| Parameter | Default | Effect |
|-----------|---------|--------|
| `DROP_ENERGY_PERCENTILE` | 85 | Higher → fewer, more intense drops detected |
| `DROP_MIN_DURATION_SEC` | 4.0 | Minimum length a high-energy section must sustain |
| `VOCAL_ENERGY_PERCENTILE` | 75 | Lower → more sensitive to quiet vocals. Relative to each track's own vocal-stem loudness rather than a fixed number, since Demucs stem levels vary a lot by mix |
| `VOCAL_MIN_DURATION_SEC` | 4.0 | Minimum vocal run length to label as VOCAL |
| `BUILD_LOOKBACK_SEC` | 16.0 | How far before a drop to look for a rising build |

For EDM: default settings work well. For hip-hop/R&B: lower `VOCAL_ENERGY_PERCENTILE` to ~60.

---

## Cue accuracy

### Beat grid

Cues snap to **Rekordbox's own beat grid and bar positions** whenever the track has already been analysed in Rekordbox (read-only, from a temp copy of `master.db` and the track's ANLZ files). That is the grid you see in Rekordbox — including any manual corrections — and unlike librosa's tracker it knows which beat is the downbeat. Bar-start cues (INTRO, DROP, BUILD, BREAK, SECTION, OUTRO) land exactly on a Rekordbox bar start; VOCAL snaps to the nearest beat since sung entries often start on a pickup.

Tracks Rekordbox hasn't analysed fall back to librosa, whose grid runs ~20–60 ms late and whose bar phase is a guess. **Analyse your library in Rekordbox first** for the best placement. Pass `--no-rekordbox-grid` to disable.

The analysis cache key includes the Rekordbox grid's identity, so re-analysing a track (or editing its grid) in Rekordbox invalidates its cached cues.

### Drops, breaks and builds

With vocal detection on (the default), drops come from Demucs' **drum stem**: a drop is where the kick returns after a kick-free stretch of 4+ seconds *and* the mix gets louder when it does. The cue sits on the first real kick, so hat/snare pickups before the drop don't pull it early. Tracks with no such gap (continuous-kick pop, say) and `--no-lyrics` runs fall back to the older loudness-based detector. Demucs separates all four stems regardless, so this costs nothing extra over vocal detection.

The same kick structure gives the other two:

- **BREAK** is the first beat with no kick where the kick later returns (not the intro, and not the outro). Longest breakdowns are kept first.
- **BUILD** is where the 2–10 kHz band starts a sustained climb (6+ dB over 4+ seconds, peaking within 8 s of the drop). Overall loudness is a poor signal here — mastered tracks stay flat or dip going into a drop — so it isn't used. The onset is rounded to a whole number of 4-bar phrases before the drop, and a BUILD that coincides with a BREAK is dropped as redundant.

BUILD is the least validated of the three: on 10 test tracks its agreement with Rekordbox's pre-chorus "Up" phrase was 4/9 cues, so treat it as a suggestion and use the truth-file workflow below to check it on your own library. Without stems, BREAK and BUILD fall back to the older loudness-based detectors.

Pass `--stem-cache DIR` to keep the vocal and drum stems (~50 MB per track) so re-analysing after a detector change skips the ~1x-realtime separation.

On 10 electro/house/dance tracks, drum-stem drops landed on a Rekordbox phrase start 87% of the time versus 32% for the loudness detector, and on a Rekordbox chorus start 52% versus 23%. Rekordbox's chorus is a heuristic, not ground truth — use the truth-file workflow below for real accuracy.

### Measuring accuracy

```bash
python main.py evaluate -n 30
python main.py evaluate -n 10 --with-stems --stem-cache stems   # also scores drum-stem drops (slow the first time)
```

Samples tracks from your Rekordbox library and reports, for the old (own-grid) and new (Rekordbox-grid) pipelines: how many cues sit on a Rekordbox beat, on a bar start, and on/near a Rekordbox phrase boundary, plus how far Soundwave's own beat tracker is from Rekordbox's. These measure **grid and phrase alignment only** — they can't say the right section was found.

For true accuracy, build a truth file once and re-run it after every detector change:

```bash
python main.py evaluate -n 20 --write-truth-template truth.json   # predicted cues for 20 tracks
# edit truth.json: fix times, delete wrong cues, add missed ones
python main.py evaluate --truth truth.json                         # hit rates @50ms / 250ms / 1 bar, misses, false positives
```

---

## Running tests

```bat
venv\Scripts\activate
pip install -r requirements-dev.txt
pytest tests\ -v
```

Tests use **synthetic audio only** (numpy-generated sine waves) — no real music files needed, no Demucs model downloaded during tests.

```
tests/
├── conftest.py             ← shared fixtures (synthetic audio signals)
├── test_analyzer.py        ← drop/build/breakdown/intro/dedup logic
├── test_drum_drops.py      ← drum-stem drop detection (synthetic kick/riser signals)
├── test_break_build.py     ← kick-structure breaks and high-band builds
├── test_rekordbox_grid.py  ← Rekordbox-grid snapping, cue-placement metrics
├── test_xml_handler.py     ← XML generation, URI conversion, merge/roundtrip
├── test_regressions.py     ← library/similarity/playlist/batch-analyze regressions
└── test_server_security.py ← web app host/origin guards, audio-only file serving
```

Run with coverage:

```bat
pytest tests\ -v --cov=soundwave --cov-report=term-missing
```

---

## Project structure

```
Soundwave/
├── soundwave/
│   ├── analyzer.py          ← audio analysis (librosa + Demucs)
│   ├── evaluate.py          ← cue-placement metrics (vs Rekordbox analysis / truth file)
│   ├── eval_runner.py       ← runs the evaluation over tracks (`main.py evaluate`)
│   ├── config.py            ← all tunable thresholds
│   ├── library.py, lookup.py, lyrics.py, similarity.py
│   ├── rekordbox/
│   │   ├── anlz_grid.py     ← reads Rekordbox's own beat grid/phrases (read-only)
│   │   ├── models.py        ← CuePoint, TrackAnalysis dataclasses
│   │   └── xml_handler.py   ← Rekordbox XML read/write
│   └── *.html / *.js / *.css, albums/   ← unrelated legacy Express demo site (see below)
├── webapp/
│   ├── server.py            ← FastAPI backend (127.0.0.1 only)
│   └── static/              ← browser UI
├── tests/
├── tools/gen_icon.py
├── main.py                  ← CLI entry point
├── run_app.bat / setup.bat  ← Windows launchers
├── requirements.txt
└── requirements-dev.txt
```

### Security notes

- The web app has no login: it can read, rename and delete files by path. It binds to `127.0.0.1` only and rejects non-loopback `Host` headers and cross-site requests. Do not change the bind address or expose the port.
- Your AcoustID key and last-used folder are stored in `~/.soundwave/config.json`, outside the repo. Never commit keys.

### Legacy demo site (`soundwave/*.html`, `app.js`, `playlists.js`)

An older, unrelated Express + MongoDB music demo lives alongside the Python package. It needs `MONGODB_URI` set in the environment (no credentials are stored in the repo) and `npm install` inside `soundwave/`. Its tracks played from `soundwave/Songs/*.mp3`, which are no longer shipped (copyrighted audio); add your own files there if you want the player to work.

### License

No license file is included, so by default all rights are reserved. Add one (for example MIT) if you want others to be able to use the code.
