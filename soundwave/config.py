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
MAX_SECTIONS = 3                   # Keep up to N section markers per track

# --- Outro detection ---
OUTRO_BARS = [32, 16]              # Place cues at last 32 bars and last 16 bars

# --- Beat snapping ---
SNAP_TO_BEAT = True                # Snap all cues to the nearest beat after detection
SNAP_DROPS_TO_BARS = 1             # Snap drops to nearest N-bar boundary. Was 4 (nearest full phrase),
                                    # which could drag a cue up to 2 bars from the actual hit — audibly
                                    # "off". 1 bar keeps the DJ-friendly downbeat snap without the drift.
SNAP_OTHERS_TO_BEATS = 1           # Snap other cues to nearest N-beat boundary

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
