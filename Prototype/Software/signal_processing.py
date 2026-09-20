"""Reference DSP for SHRUTI (DPOAE, 2f1 - f2).

This module implements the *design* described in the project presentation so it can
be tested in software before the ESP32-S3 firmware exists:

* 48 kHz sampling, 8192-point FFT (5.859375 Hz per bin)
* stimulus frequencies f1, f2 and 2f1 - f2 placed on exact FFT bin centres, so the
  65 dB SPL primaries leak nothing into the emission bin (rectangular window)
* phase-locked coherent (time-domain) averaging, split into A/B halves
* in-situ probe-fit check and per-frequency level calibration
* frame rejection against a noise reference measured in the ear before the test
* per-frequency classification: emission present / absent / indeterminate

It is deliberately pure NumPy with no knowledge of the simulator, so it can be ported to
C/C++ (ESP-DSP) later. All pressures are in pascals (rms unless stated).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Design constants (from the internal-round presentation unless noted)
# ---------------------------------------------------------------------------
FS = 48_000
NFFT = 8192
BIN_HZ = FS / NFFT                      # 5.859375 Hz
P_REF = 20e-6                           # 0 dB SPL, in pascals
F2_NOMINAL_HZ = (2000, 3000, 4000, 5000)
F2_F1_RATIO = 1.22
L1_DB = 65.0
L2_DB = 55.0
SNR_CRITERION_DB = 6.0                  # emission counts as present at >= 6 dB SNR
EMISSION_REF_DB = 0.0                   # target emission level, about 0 dB SPL
FRAMES_PER_FREQ = 23                    # 23 frames x 8192 / 48 kHz = 3.93 s (~4 s)
PASS_FREQS_REQUIRED = 3                 # PASS needs emission at >= 3 of 4 frequencies

# ---------------------------------------------------------------------------
# Simulator-level choices (NOT taken from the presentation; tune with bench data)
# ---------------------------------------------------------------------------
MIN_VALID_FRAMES = 12                   # fewer clean frames -> frequency indeterminate
MAX_FRAMES_PER_FREQ = 46                # keep acquiring until 23 clean frames, or stop here (~8 s)
DETECTION_MARGIN_DB = 6.0               # allowance for emission variability when judging "testable"
AB_MIN_SNR_DB = 3.0                     # each A/B half must show >= 3 dB SNR
REJECT_RATIO = 1.5                      # reject frame if residual power > 1.5x baseline
NOISE_SPAN_BINS = 30                    # neighbour bins used for the noise estimate
NOISE_GUARD_BINS = 2                    # bins skipped around the emission / primaries
MAX_CAL_CORRECTION_DB = 15.0            # larger correction -> probe refit
CAL_TOLERANCE_DB = 1.5                  # verify calibrated level within +/- 1.5 dB
FIT_GOOD_DB = 6.0                       # level deficit up to this is a good seal
FIT_REJECT_DB = 15.0                    # level deficit above this is refused
# A noise floor above this cannot reliably reveal a ~0 dB SPL emission: the device abstains.
DETECTABLE_FLOOR_DB = EMISSION_REF_DB - SNR_CRITERION_DB - DETECTION_MARGIN_DB
RESIDUAL_BAND_HZ = (1000.0, 6000.0)
DISPLAY_MAX_HZ = 6000.0


# ---------------------------------------------------------------------------
# Frequency plan: everything on exact bins
# ---------------------------------------------------------------------------
def _round_half_up(x: float) -> int:
    return int(np.floor(x + 0.5))


@dataclass(frozen=True)
class FreqPlan:
    label: str
    k1: int
    k2: int
    kdp: int

    @property
    def f1_hz(self) -> float:
        return self.k1 * BIN_HZ

    @property
    def f2_hz(self) -> float:
        return self.k2 * BIN_HZ

    @property
    def fdp_hz(self) -> float:
        return self.kdp * BIN_HZ


def build_plan(f2_nominal=F2_NOMINAL_HZ, ratio: float = F2_F1_RATIO) -> List[FreqPlan]:
    """Choose integer bins k2 ~ f2, k1 ~ k2/ratio, then kdp = 2*k1 - k2 exactly."""
    plans = []
    for f2n in f2_nominal:
        k2 = _round_half_up(f2n / BIN_HZ)
        k1 = _round_half_up(k2 / ratio)
        plans.append(FreqPlan(label=f"{f2n / 1000:g} kHz", k1=k1, k2=k2, kdp=2 * k1 - k2))
    return plans


PLAN: List[FreqPlan] = build_plan()


# ---------------------------------------------------------------------------
# Level helpers
# ---------------------------------------------------------------------------
def db_spl_to_pa_rms(level_db: float) -> float:
    return P_REF * 10.0 ** (level_db / 20.0)


def power_to_db_spl(mean_square_pa2):
    return 10.0 * np.log10(np.maximum(mean_square_pa2, 1e-30) / P_REF ** 2)


def bin_power(spectra: np.ndarray) -> np.ndarray:
    """Mean-square pressure (Pa^2) represented by each FFT bin (rectangular window)."""
    return (2.0 * np.abs(spectra) / NFFT) ** 2 / 2.0


def frames_to_spectra(frames: np.ndarray) -> np.ndarray:
    """rfft of each frame. Rectangular window: bin-exact tones have zero leakage."""
    return np.fft.rfft(frames, axis=-1)


def _residual_mask() -> np.ndarray:
    lo = int(np.ceil(RESIDUAL_BAND_HZ[0] / BIN_HZ))
    hi = int(np.floor(RESIDUAL_BAND_HZ[1] / BIN_HZ))
    mask = np.zeros(NFFT // 2 + 1, dtype=bool)
    mask[lo:hi + 1] = True
    for p in PLAN:
        for k in (p.k1, p.k2, p.kdp):
            mask[max(0, k - NOISE_GUARD_BINS):k + NOISE_GUARD_BINS + 1] = False
    return mask


_RESIDUAL_MASK = _residual_mask()


def residual_power(spectra: np.ndarray) -> np.ndarray:
    """Per-frame mean-square pressure in the residual band (tones masked out)."""
    return bin_power(spectra[..., _RESIDUAL_MASK]).mean(axis=-1)


def _noise_indices(plan: FreqPlan) -> np.ndarray:
    lo = plan.kdp - NOISE_SPAN_BINS
    hi = plan.kdp + NOISE_SPAN_BINS
    idx = np.arange(lo, hi + 1)
    keep = np.abs(idx - plan.kdp) > NOISE_GUARD_BINS
    for k in (plan.k1, plan.k2):
        keep &= np.abs(idx - k) > NOISE_GUARD_BINS
    return idx[keep]


def display_spectrum_db(spectrum: np.ndarray) -> List[float]:
    """Spectrum in dB SPL from 0 Hz to DISPLAY_MAX_HZ, one value per FFT bin."""
    top = int(DISPLAY_MAX_HZ / BIN_HZ)
    return np.round(power_to_db_spl(bin_power(spectrum[: top + 1])), 1).tolist()


def local_spectrum_db(spectrum: np.ndarray, plan: FreqPlan, half_width: int = 40) -> List[float]:
    lo, hi = plan.kdp - half_width, plan.kdp + half_width + 1
    return np.round(power_to_db_spl(bin_power(spectrum[lo:hi])), 1).tolist()


# ---------------------------------------------------------------------------
# Capture interface (hardware abstraction: real codec or the simulator)
# ---------------------------------------------------------------------------
class CaptureSource(Protocol):
    def capture_silence(self, n_frames: int) -> np.ndarray:
        """n_frames x NFFT microphone samples (Pa) with no stimulus playing."""

    def capture_stimulus(self, i: int, n_frames: int, drive_gain_db: Tuple[float, float]) -> np.ndarray:
        """n_frames x NFFT microphone samples (Pa) while playing frequency pair i."""


# ---------------------------------------------------------------------------
# Probe fit, calibration, noise check
# ---------------------------------------------------------------------------
def primary_levels_db(frame: np.ndarray, plan: FreqPlan) -> Tuple[float, float]:
    X = frames_to_spectra(frame.reshape(-1, NFFT)).mean(axis=0)
    p = bin_power(X)
    return float(power_to_db_spl(p[plan.k1])), float(power_to_db_spl(p[plan.k2]))


def probe_fit(source: CaptureSource, plan: FreqPlan) -> Dict:
    """Play the primaries at nominal drive and compare with the expected in-ear level."""
    l1, l2 = primary_levels_db(source.capture_stimulus(PLAN.index(plan), 1, (0.0, 0.0)), plan)
    deficit = ((L1_DB - l1) + (L2_DB - l2)) / 2.0
    status = "good" if deficit <= FIT_GOOD_DB else "marginal" if deficit <= FIT_REJECT_DB else "reject"
    return {"status": status, "deficit_db": round(float(deficit), 2)}


def calibrate(source: CaptureSource) -> Dict:
    """Per-frequency in-situ calibration: set the drive so the ear sees 65/55 dB SPL."""
    gains: List[Tuple[float, float]] = []
    for i, plan in enumerate(PLAN):
        l1, l2 = primary_levels_db(source.capture_stimulus(i, 1, (0.0, 0.0)), plan)
        g = (L1_DB - l1, L2_DB - l2)
        if max(abs(g[0]), abs(g[1])) > MAX_CAL_CORRECTION_DB:
            return {"ok": False, "reason": "calibration correction out of range", "gains_db": None}
        v1, v2 = primary_levels_db(source.capture_stimulus(i, 1, g), plan)
        if abs(v1 - L1_DB) > CAL_TOLERANCE_DB or abs(v2 - L2_DB) > CAL_TOLERANCE_DB:
            return {"ok": False, "reason": "calibration did not converge", "gains_db": None}
        gains.append(g)
    return {"ok": True, "reason": None, "gains_db": [tuple(round(x, 2) for x in g) for g in gains]}


def noise_reference(source: CaptureSource, n_frames: int = 6) -> Dict:
    """Measure the noise before stimulating. The reference is the *quietest* of a few frames
    (plus 5 %) so that a burst of crying or movement during the check does not inflate it."""
    X = frames_to_spectra(source.capture_silence(n_frames))
    resid = float(residual_power(X).min()) * 1.05
    bin_db = float(power_to_db_spl(resid))
    return {
        "residual_power": resid,
        "noise_bin_db": round(bin_db, 2),
        "expected_floor_db": round(bin_db - 10 * np.log10(FRAMES_PER_FREQ), 2),
        "detectable_floor_db": DETECTABLE_FLOOR_DB,
    }


# ---------------------------------------------------------------------------
# Per-frequency measurement
# ---------------------------------------------------------------------------
def _level_and_noise(X: np.ndarray, plan: FreqPlan) -> Tuple[float, float]:
    p = bin_power(X)
    dp_db = float(power_to_db_spl(p[plan.kdp]))
    noise_db = float(power_to_db_spl(p[_noise_indices(plan)].mean()))
    return dp_db, noise_db


def valid_frame_mask(spectra: np.ndarray, baseline_residual: float) -> np.ndarray:
    return residual_power(spectra) <= REJECT_RATIO * baseline_residual


def analyse_frequency(spectra_valid: np.ndarray, plan: FreqPlan, n_total: int) -> Dict:
    """Coherent average (A/B split) of the accepted frames and emission decision."""
    n_valid = int(len(spectra_valid))
    base = {
        "label": plan.label,
        "f1_hz": round(plan.f1_hz, 2), "f2_hz": round(plan.f2_hz, 2), "fdp_hz": round(plan.fdp_hz, 2),
        "frames_total": int(n_total), "frames_valid": n_valid,
    }
    if n_valid < 2:
        return {**base, "dp_db": None, "noise_db": None, "snr_db": None, "snr_a_db": None, "snr_b_db": None,
                "primary_db": None, "outcome": "indeterminate", "reason": "no clean frames", "testable": False}
    X = spectra_valid.mean(axis=0)
    XA, XB = spectra_valid[0::2].mean(axis=0), spectra_valid[1::2].mean(axis=0)
    dp_db, noise_db = _level_and_noise(X, plan)
    dpa, na = _level_and_noise(XA, plan)
    dpb, nb = _level_and_noise(XB, plan)
    snr, snr_a, snr_b = dp_db - noise_db, dpa - na, dpb - nb
    present = snr >= SNR_CRITERION_DB and snr_a >= AB_MIN_SNR_DB and snr_b >= AB_MIN_SNR_DB
    testable = noise_db <= DETECTABLE_FLOOR_DB
    if n_valid < MIN_VALID_FRAMES:
        outcome, reason = "indeterminate", "too few clean frames"
    elif present:
        outcome, reason = "present", None
    elif testable:
        outcome, reason = "absent", None
    else:
        outcome, reason = "indeterminate", "noise floor too high to rule an emission in or out"
    p = bin_power(X)
    return {
        **base,
        "dp_db": round(dp_db, 2), "noise_db": round(noise_db, 2), "snr_db": round(snr, 2),
        "snr_a_db": round(snr_a, 2), "snr_b_db": round(snr_b, 2),
        "primary_db": [round(float(power_to_db_spl(p[plan.k1])), 2), round(float(power_to_db_spl(p[plan.k2])), 2)],
        "outcome": outcome, "reason": reason, "testable": bool(testable),
    }
