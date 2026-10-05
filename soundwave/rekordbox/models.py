"""Shared data models."""

from dataclasses import dataclass, field
from typing import List


@dataclass
class CuePoint:
    label: str          # DROP, LYRIC, BUILD, BREAK, INTRO
    time_sec: float
    color: int          # 0xRRGGBB


@dataclass
class TrackAnalysis:
    audio_path: str
    title: str
    artist: str
    duration_sec: float
    bpm: float
    cues: List[CuePoint] = field(default_factory=list)
