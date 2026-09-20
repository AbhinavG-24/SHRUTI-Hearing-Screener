"""Simulation studies run against the synthetic ear model.

These numbers describe the *simulated* pipeline under the simulator's assumptions. They are
software-verification results, not device performance, and must never be presented as
bench, adult-volunteer or neonatal evidence (see Documentation/Testing).
"""
from __future__ import annotations

from collections import Counter
from typing import Dict, List

import signal_processing as dsp
from ear_simulator import SimulatedEar
from screening import run_ear
from signal_processing import EMISSION_REF_DB, SNR_CRITERION_DB

DISTORTION_LIMIT_DB = -15.0      # presentation, Tier 1 criterion


def _verdicts(n: int, seed0: int, **ear_kwargs) -> Dict[str, int]:
    c = Counter(run_ear(SimulatedEar(seed=seed0 + i, **ear_kwargs))["verdict"] for i in range(n))
    return {v: c.get(v, 0) for v in ("PASS", "REFER", "RETEST")}


def sealed_cavity_trials(n: int = 120, seed0: int = 1000) -> Dict:
    """Simulated Tier-1-style trial: a sealed cavity cannot emit, so any PASS is a false PASS."""
    counts = _verdicts(n, seed0, ohc=0, seal=1.0, ambient_db=30, rig="two")
    return {"trials": n, "false_pass": counts["PASS"], "counts": counts,
            "criterion": "<= 1 PASS in 120 repetitions (from the presentation)"}


SELF_TEST_FRAMES = 92                    # 4x the screening average: a self-test may take longer


def self_test(rig: str = "two", seed: int = 7) -> Dict:
    """Distortion-floor check in a sealed cavity (no emission possible).

    Longer averaging (92 frames, about 16 s per frequency) and noise-power subtraction are
    needed: with the 23-frame screening average the noise floor (about -19 dB SPL) is too close
    to a -15 dB SPL limit to resolve the system's own 2f1-f2 product reliably.
    Reported value is the worst of four frequencies, bounded below by the noise floor."""
    ear = SimulatedEar(ohc=0, seal=1.0, ambient_db=30, rig=rig, seed=seed)
    cal = dsp.calibrate(ear)
    base = dsp.noise_reference(ear)
    worst, noises = -99.0, []
    for i, plan in enumerate(dsp.PLAN):
        spectra = dsp.frames_to_spectra(ear.capture_stimulus(i, SELF_TEST_FRAMES, cal["gains_db"][i]))
        X = spectra.mean(axis=0)
        p = dsp.bin_power(X)
        p_noise = float(p[dsp._noise_indices(plan)].mean())
        p_dp = max(float(p[plan.kdp]) - p_noise, 0.0)
        level = float(dsp.power_to_db_spl(max(p_dp, p_noise)))    # cannot claim better than the noise floor
        worst = max(worst, level)
        noises.append(float(dsp.power_to_db_spl(p_noise)))
    return {"rig": rig, "distortion_floor_db": round(worst, 1), "limit_db": DISTORTION_LIMIT_DB,
            "margin_db": round(DISTORTION_LIMIT_DB - worst, 1), "in_service": worst <= DISTORTION_LIMIT_DB,
            "noise_floor_db": round(sum(noises) / 4, 1), "frames": SELF_TEST_FRAMES}


def sweep_ohc(n: int = 30, seed0: int = 2000) -> List[Dict]:
    out = []
    for ohc in (0, 20, 40, 60, 80, 100):
        c = _verdicts(n, seed0 + ohc * 100, ohc=ohc)
        out.append({"ohc": ohc, "trials": n, **c})
    return out


def sweep_ambient(n: int = 30, seed0: int = 3000) -> List[Dict]:
    out = []
    for amb in (30, 45, 55, 60, 65, 70, 75):
        c = _verdicts(n, seed0 + amb * 100, ohc=100, ambient_db=amb)
        out.append({"ambient_db": amb, "trials": n, **c})
    return out


def sweep_artifacts(n: int = 30, seed0: int = 4000) -> List[Dict]:
    out = []
    for art in ("none", "crying", "movement"):
        c = _verdicts(n, seed0, ohc=100, artifact=art)
        out.append({"artifact": art, "trials": n, **c})
    return out


def run_study(n: int = 30) -> Dict:
    return {
        "notice": "Simulation only. Synthetic ear model; not evidence of device performance.",
        "assumptions": {"snr_criterion_db": SNR_CRITERION_DB, "target_emission_db": EMISSION_REF_DB},
        "sealed_cavity": sealed_cavity_trials(120),
        "self_test": {"two": self_test("two"), "one": self_test("one")},
        "healthy_vs_ohc": sweep_ohc(n),
        "healthy_vs_ambient": sweep_ambient(n),
        "healthy_vs_artifact": sweep_artifacts(n),
    }
