"""
Shared pytest fixtures — synthetic audio generation.
No real music files required.
"""

import numpy as np
import pytest


SR = 22050  # sample rate for all synthetic signals


def _sine(freq: float, duration_sec: float, amplitude: float = 1.0) -> np.ndarray:
    t = np.linspace(0, duration_sec, int(SR * duration_sec), endpoint=False)
    return (np.sin(2 * np.pi * freq * t) * amplitude).astype(np.float32)


def _bass(duration_sec: float, amplitude: float = 1.0) -> np.ndarray:
    """Low-frequency content (100 Hz) to help trigger drop detection."""
    return _sine(100, duration_sec, amplitude)


@pytest.fixture
def sr():
    return SR


@pytest.fixture
def signal_with_drop():
    """
    60-second synthetic track:
      0–20s   quiet intro     (amp 0.05)
      20–32s  rising build    (amp ramps 0.05 → 0.7)
      32–52s  loud drop       (amp 0.9 + bass 1.0)
      52–60s  quiet breakdown (amp 0.03)
    """
    quiet = _sine(440, 20.0, 0.05)

    n_build = int(SR * 12)
    envelope = np.linspace(0.05, 0.7, n_build)
    build = (_sine(440, 12.0) * envelope).astype(np.float32)

    drop = _sine(440, 20.0, 0.9) + _bass(20.0, 1.0)
    drop = np.clip(drop, -1.0, 1.0).astype(np.float32)

    breakdown = _sine(440, 8.0, 0.03)

    y = np.concatenate([quiet, build, drop, breakdown])
    return y, SR


@pytest.fixture
def constant_signal():
    """Flat, medium-energy signal — should produce no drops or breakdowns."""
    return _sine(440, 30.0, 0.3), SR


@pytest.fixture
def signal_with_breakdown():
    """
    42-second track with a clear 12-second silence in the middle.
      0–15s  loud section
      15–27s true digital silence (should be BREAK)
      27–42s loud section
    The 12s silence is ~29% of the track so it clearly dominates the
    25th-percentile bucket used by the breakdown detector.
    """
    loud = _sine(440, 15.0, 0.9) + _bass(15.0, 0.8)
    loud = np.clip(loud, -1.0, 1.0).astype(np.float32)
    silence = np.zeros(int(SR * 12.0), dtype=np.float32)
    loud2 = _sine(440, 15.0, 0.9) + _bass(15.0, 0.8)
    loud2 = np.clip(loud2, -1.0, 1.0).astype(np.float32)
    y = np.concatenate([loud, silence, loud2])
    return y, SR
