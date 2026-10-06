"""
Tunable detection parameters. Adjust to match your genre/taste.
"""

# --- Drop detection ---
DROP_ENERGY_PERCENTILE = 85        # RMS percentile a frame must exceed to be a drop candidate
DROP_ENERGY_PERCENTILE_FALLBACK = 70   # Retried if the strict percentile finds nothing (low-dynamic-range tracks)
DROP_ENERGY_PERCENTILE_FALLBACK2 = 55  # Last resort for heavily mastered/compressed pop tracks, where even
                                        # 70th-percentile candidates rarely sustain a continuous run
DROP_BASS_WEIGHT = 1.5             # Extra weight on low-frequency energy (60–250 Hz)
DROP_SMOOTH_SEC = 1.0               # Moving-average window applied before run detection. Without this, a
                                     # single quiet 100ms frame (a gap between kick hits, a hi-hat trough)
                                     # breaks an otherwise-solid drop run — on real tracks this made
                                     # DROP_MIN_DURATION_SEC of *uninterrupted* frames almost impossible to
                                     # reach, so drops went undetected far more often than they should.
DROP_MIN_DURATION_SEC = 4.0        # Minimum seconds a high-energy run must sustain
DROP_MERGE_GAP_SEC = 2.0           # Merge drop candidates within this many seconds
MAX_DROPS = 4                      # Keep only the strongest N drops per track

# --- Drum-stem drop detection (used when Demucs stems are available) ---
# A drop is where the kick comes back after a gap, and the mix gets louder.
# Kick presence is measured on the drum stem's low band, since breakdowns
# often keep hats/percussion going and only drop the kick.
DRUM_HOP = 512                     # Samples per frame (~12-23 ms) — fine enough to locate the first kick
DRUM_KICK_LOW_HZ = 30
DRUM_KICK_HIGH_HZ = 150
DRUM_PRESENCE_SMOOTH_SEC = 0.5     # Smoothing for presence/gap detection only; the drop time itself is refined on unsmoothed frames
DRUM_PRESENCE_FRACTION = 0.2       # Kick counts as "present" above this fraction of the track's 90th-percentile kick level
DRUM_GAP_MIN_SEC = 4.0             # Kick-free stretch that counts as a break (~2 bars at 120 BPM)
DRUM_ONSET_FRACTION = 0.5          # First kick = first frame above this fraction of the peak in the next 2 s
DROP_MIN_CONTRAST = 1.2            # Mix must be this much louder after the kick returns than before, or it isn't a drop
DROP_CONTRAST_BEFORE_SEC = 16.0    # ...vs the kick-free gap before it (up to this long)
DROP_CONTRAST_AFTER_SEC = 8.0

# --- Build detection from the high band (used with drum stems) ---
# Mastered tracks keep their overall loudness flat (or dip for a bar) going
# into a drop, so a loudness slope misses most builds. Risers, snare rolls
# and filter sweeps show up as a climb in the 2-10 kHz band instead.
BUILD_BAND_LOW_HZ = 2000
BUILD_BAND_HIGH_HZ = 10000
BUILD_SMOOTH_SEC = 1.0
BUILD_MAX_SEC = 32.0               # How far before a drop a build can start
BUILD_MIN_SEC = 4.0                # Shortest climb that counts (~2 bars)
BUILD_MIN_RISE_DB = 6.0            # High-band level must climb at least this much
BUILD_PEAK_MAX_GAP_SEC = 8.0       # The climb must peak this close to the drop (not far earlier)

# --- Vocal entry detection ---
# Relative to each track's own vocal-stem loudness (percentile of its RMS
# distribution) rather than a fixed absolute number — Demucs stem loudness
# varies a lot by mix/mastering, so an absolute threshold either misses
# quiet vocals or fires on bleed in loud mixes depending on the track.
VOCAL_ENERGY_PERCENTILE = 75       # RMS percentile of the vocal stem a frame must exceed
VOCAL_ENERGY_FLOOR = 0.008         # Absolute floor so near-silent stems (no vocals at all) don't get a false entry from noise
VOCAL_SMOOTH_SEC = 2.0             # Moving-average window applied before thresholding. Sung/rapped vocals have
                                    # breath and word gaps roughly every 1-2s, which — unsmoothed — broke a
                                    # continuous run long before VOCAL_MIN_DURATION_SEC was reached; vocals need
                                    # more smoothing than drops (DROP_SMOOTH_SEC) because those gaps are shorter
                                    # and more frequent than the beat-level dips in an instrumental section.
VOCAL_MIN_DURATION_SEC = 4.0       # Minimum continuous vocal presence to label VOCAL
VOCAL_MERGE_GAP_SEC = 3.0
MAX_VOCAL_ENTRIES = 3              # Keep up to N distinct vocal entries (verse/chorus starts), not just the first

# --- Build detection ---
BUILD_LOOKBACK_SEC = 16.0          # How far before a drop to look for rising energy
BUILD_MIN_SLOPE = 0.003            # Minimum energy slope (per second) to qualify as a build
BUILD_RISE_FRACTION = 0.2          # Where in the low->high energy climb to place the cue (0 = lookback start, 1 = drop) — placed near the start of the actual rise, not just a fixed offset

# --- Breakdown detection ---
BREAK_ENERGY_PERCENTILE = 25       # RMS percentile below which a frame is a breakdown candidate
BREAK_MIN_DURATION_SEC = 8.0
BREAK_MERGE_GAP_SEC = 2.0
MAX_BREAKS = 3                     # Keep only the deepest N breakdowns per track

# --- Generic section/transition detection (catches structural changes the
# --- energy-percentile detectors above miss, e.g. verse->chorus in tracks
# --- without a clean EDM-style drop) ---
SECTION_NOVELTY_PERCENTILE = 90    # Spectral-novelty percentile a frame must exceed
SECTION_MIN_GAP_SEC = 20.0         # Minimum spacing between section markers, and from other cues
SECTION_MIN_START_SEC = 4.0        # Ignore novelty peaks this close to the start (that's just the track beginning)
MAX_SECTIONS = 3                   # Keep up to N section markers per track

# --- Outro detection ---
OUTRO_BARS = [32, 16]              # Place cues at last 32 bars and last 16 bars

# --- Beat snapping ---
SNAP_TO_BEAT = True                # Snap all cues to the nearest beat after detection
SNAP_DROPS_TO_BARS = 1             # Snap drops to nearest N-bar boundary. Was 4 (nearest full phrase),
                                    # which could drag a cue up to 2 bars from the actual hit — audibly
                                    # "off". 1 bar keeps the DJ-friendly downbeat snap without the drift.
SNAP_OTHERS_TO_BEATS = 1           # Snap other cues to nearest N-beat boundary

# Cues that mark the start of a bar/phrase. When real downbeats are known
# (Rekordbox grid) these snap to the nearest downbeat instead of any beat.
# VOCAL is excluded: sung entries often start on a pickup before the bar.
DOWNBEAT_SNAP_LABELS = {"INTRO", "DROP", "BUILD", "BREAK", "SECTION", "OUTRO32", "OUTRO16"}

# --- General ---
FRAME_HOP_SEC = 0.1                # Analysis resolution in seconds
MAX_HOT_CUES = 8                   # Rekordbox supports up to 8 hot cues per track

# --- Hot cue RGB colors (Rekordbox palette) ---
CUE_COLORS = {
    "INTRO":   0xFFFFFF,  # White
    "DROP":    0xFF0000,  # Red
    "BUILD":   0x00FF00,  # Green
    "BREAK":   0xFFFF00,  # Yellow
    "VOCAL":   0x0000FF,  # Blue
    "OUTRO32": 0xFF6400,  # Orange
    "OUTRO16": 0xFF0080,  # Pink/Rose
    "SECTION": 0x9932CC,  # Purple
}

# When there are more candidate cues than MAX_HOT_CUES, selection works like
# this (see analyzer._select_by_importance):
#   1. INTRO is always kept.
#   2. Remaining slots are filled round-robin across CONTENT_CUE_TYPES, one
#      per type per round, strongest-first within each type — so a track
#      with many drops can't crowd out its only build or vocal entry.
#   3. OUTRO16 / OUTRO32 are structural navigation aids, not analyzed
#      content, so they only fill genuinely leftover slots.
CONTENT_CUE_TYPES = ["DROP", "SECTION", "BUILD", "VOCAL", "BREAK"]
