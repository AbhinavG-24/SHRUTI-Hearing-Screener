"""Synthetic ear / probe / noise model that feeds the reference DSP.

This is a *test rig*, not physiology. It exists so the DSP and decision logic can be
exercised end to end before hardware exists. Every constant marked "illustrative" is a
modelling choice and must be replaced by bench measurements when they are available.

Values taken from the presentation: microphone self-noise (27 dB(A) EIN -> about -5.3 dB
SPL in a 5.86 Hz bin), 65/55 dB SPL primaries, target emission about 0 dB SPL, system
distortion limit -15 dB SPL.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

import numpy as np

from signal_processing import (
    BIN_HZ, FS, L1_DB, L2_DB, NFFT, PLAN, P_REF, db_spl_to_pa_rms,
)

MIC_NOISE_BIN_DB = -5.3          # dB SPL per 5.86 Hz bin (presentation, noise budget)
HEALTHY_DP_DB = 0.0              # target emission, about 0 dB SPL (presentation)
TWO_RECEIVER_DISTORTION_DB = -22.0   # illustrative; must stay <= -15 dB SPL (presentation, Tier 1)
SHARED_TRANSDUCER_DISTORTION_DB = 4.0  # illustrative: one transducer intermodulates the tones itself
AMBIENT_BANDWIDTH_HZ = 3000.0    # illustrative: ambient assumed flat over this band
SEAL_LEVEL_LOSS_DB = 30.0        # illustrative: level deficit at seal = 0 is 30 dB
ARTIFACTS = {                    # illustrative: (probability a frame is hit, extra bin level dB SPL)
    "none": (0.0, 0.0),
    "crying": (0.35, 20.0),
    "movement": (0.15, 25.0),
}
RIGS = ("two", "one")


def _sum_db(*levels_db: float) -> float:
    return float(10 * np.log10(sum(10 ** (l / 10) for l in levels_db)))


def _noise_sigma_pa(bin_level_db: float) -> float:
    """Time-domain sigma giving a mean per-bin level of bin_level_db for white Gaussian noise."""
    return float(np.sqrt(P_REF ** 2 * 10 ** (bin_level_db / 10) * NFFT / 2.0))


@dataclass
class SimulatedEar:
    ohc: float = 100.0            # outer-hair-cell function, 0-100 % (illustrative scale)
    ambient_db: float = 38.0      # ambient noise, dB(A)
    seal: float = 0.88            # probe seal quality, 0-1
    rig: str = "two"              # "two" receivers (design) or "one" shared transducer (the trap)
    artifact: str = "none"
    seed: Optional[int] = None
    rng: np.random.Generator = field(init=False, repr=False)

    def __post_init__(self):
        if self.rig not in RIGS:
            raise ValueError(f"rig must be one of {RIGS}")
        if self.artifact not in ARTIFACTS:
            raise ValueError(f"artifact must be one of {tuple(ARTIFACTS)}")
        self.ohc = float(np.clip(self.ohc, 0, 100))
        self.seal = float(np.clip(self.seal, 0, 1))
        self.rng = np.random.default_rng(self.seed)
        n = len(PLAN)
        self._emission_offset = self.rng.normal(0.0, 2.0, n)     # per-frequency emission variability
        self._phi1 = self.rng.uniform(0, 2 * np.pi, n)
        self._phi2 = self.rng.uniform(0, 2 * np.pi, n)
        self._theta_e = self.rng.uniform(0, 2 * np.pi, n)        # cochlear phase lag
        self._theta_d = self.rng.uniform(0, 2 * np.pi, n)        # system-distortion phase
        self._distortion_jitter = self.rng.normal(0.0, 1.0, n)
        self._cal_error = self.rng.normal(0.0, 0.3, (n, 2))      # residual calibration error, dB

    # ---- physical model -------------------------------------------------
    @property
    def level_deficit_db(self) -> float:
        return SEAL_LEVEL_LOSS_DB * (1.0 - self.seal)

    @property
    def noise_bin_db(self) -> float:
        """Steady in-canal noise per bin: microphone self-noise + attenuated ambient."""
        attenuation = 12.0 + 28.0 * self.seal
        ambient_bin = self.ambient_db - 10 * np.log10(AMBIENT_BANDWIDTH_HZ / BIN_HZ) - attenuation
        return _sum_db(MIC_NOISE_BIN_DB, ambient_bin)

    def emission_db(self, i: int) -> Optional[float]:
        if self.ohc <= 0:
            return None
        return HEALTHY_DP_DB - 0.3 * (100.0 - self.ohc) + float(self._emission_offset[i])

    def distortion_db(self, i: int) -> float:
        base = TWO_RECEIVER_DISTORTION_DB if self.rig == "two" else SHARED_TRANSDUCER_DISTORTION_DB
        return base + float(self._distortion_jitter[i])

    # ---- capture interface ---------------------------------------------
    def _noise(self, n_frames: int) -> np.ndarray:
        frames = self.rng.normal(0.0, _noise_sigma_pa(self.noise_bin_db), (n_frames, NFFT))
        prob, extra_db = ARTIFACTS[self.artifact]
        if prob > 0:
            hit = self.rng.random(n_frames) < prob
            if hit.any():
                sigma = _noise_sigma_pa(_sum_db(self.noise_bin_db, extra_db))
                frames[hit] = self.rng.normal(0.0, sigma, (int(hit.sum()), NFFT))
        return frames

    def capture_silence(self, n_frames: int) -> np.ndarray:
        return self._noise(n_frames)

    def capture_stimulus(self, i: int, n_frames: int, drive_gain_db: Tuple[float, float]) -> np.ndarray:
        plan = PLAN[i]
        n = np.arange(NFFT)
        l1 = L1_DB - self.level_deficit_db + drive_gain_db[0] + (self._cal_error[i, 0] if any(drive_gain_db) else 0.0)
        l2 = L2_DB - self.level_deficit_db + drive_gain_db[1] + (self._cal_error[i, 1] if any(drive_gain_db) else 0.0)
        amp = lambda level_db: np.sqrt(2.0) * db_spl_to_pa_rms(level_db)
        phase = lambda k, ph: 2 * np.pi * k * n / NFFT + ph
        tone = amp(l1) * np.sin(phase(plan.k1, self._phi1[i])) + amp(l2) * np.sin(phase(plan.k2, self._phi2[i]))
        dp_phase = 2 * self._phi1[i] - self._phi2[i]
        e = self.emission_db(i)
        if e is not None:
            tone = tone + amp(e) * np.sin(phase(plan.kdp, dp_phase + self._theta_e[i]))
        tone = tone + amp(self.distortion_db(i)) * np.sin(phase(plan.kdp, dp_phase + self._theta_d[i]))
        return tone[None, :] + self._noise(n_frames)
