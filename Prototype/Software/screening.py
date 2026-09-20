"""Orchestrates one ear's screening: DSP stages -> events -> clinical decision.

`screen_ear_events` is a generator of plain dicts. The server replays them over a
WebSocket with pacing; tests consume them directly.
"""
from __future__ import annotations

from typing import Dict, Iterator, List, Optional

import numpy as np

import clinical_engine as clinical
import signal_processing as dsp

PHASES = ["probe_fit", "calibration", "noise_check", "stimulus", "averaging", "decision"]
SNAPSHOT_EVERY = 4


def measure_frequency(source, i: int, gains, baseline_residual: float,
                      target_frames: int = dsp.FRAMES_PER_FREQ,
                      max_frames: int = dsp.MAX_FRAMES_PER_FREQ,
                      snapshot_every: Optional[int] = None):
    """Acquire until `target_frames` clean frames are accumulated (or `max_frames` is reached),
    then average and analyse. Yields running-average snapshots and finally the analysis dict."""
    plan = dsp.PLAN[i]
    spectra = dsp.frames_to_spectra(source.capture_stimulus(i, max_frames, gains))
    valid = dsp.valid_frame_mask(spectra, baseline_residual)
    reached = np.flatnonzero(np.cumsum(valid) >= target_frames)
    stop = int(reached[0]) + 1 if len(reached) else max_frames       # frames actually acquired
    spectra, valid = spectra[:stop], valid[:stop]
    step = snapshot_every or stop
    marks = list(range(step, stop + 1, step))
    if not marks or marks[-1] != stop:
        marks.append(stop)
    for m in marks:
        ok = valid[:m]
        if ok.sum() == 0:
            continue
        avg = spectra[:m][ok].mean(axis=0)
        snap = dsp.analyse_frequency(spectra[:m][ok], plan, m)
        yield {"event": "spectrum", "freq_index": i, "frames_done": m, "frames_valid": int(ok.sum()),
               "spec_db": dsp.display_spectrum_db(avg), "local_db": dsp.local_spectrum_db(avg, plan),
               "dp_db": snap["dp_db"], "noise_db": snap["noise_db"], "snr_db": snap["snr_db"]}
    final = dsp.analyse_frequency(spectra[valid], plan, stop)
    final["local_db"] = dsp.local_spectrum_db(spectra[valid].mean(axis=0), plan) if valid.sum() else None
    yield {"event": "freq_result", "freq_index": i, "result": final}


def _conditions(source) -> Dict:
    keys = ("ohc", "ambient_db", "seal", "rig", "artifact")
    return {k: getattr(source, k) for k in keys if hasattr(source, k)}


def screen_ear_events(source, ear: str = "left", frames_per_freq: int = dsp.FRAMES_PER_FREQ,
                      snapshot_every: int = SNAPSHOT_EVERY) -> Iterator[Dict]:
    quality: Dict = {}
    per_freq: List[Dict] = []

    def finish(failure: Optional[Dict]):
        decision = clinical.decide_ear(per_freq, failure)
        return {"event": "result", "ear": ear, "verdict": decision["verdict"], "reason": decision["reason"],
                "decision": decision, "per_frequency": per_freq, "quality": quality,
                "conditions": _conditions(source)}

    # 1 probe fit
    yield {"event": "phase", "phase": "probe_fit", "status": "running"}
    fit = dsp.probe_fit(source, dsp.PLAN[2])
    quality["probe_fit"] = fit
    if fit["status"] == "reject":
        yield {"event": "phase", "phase": "probe_fit", "status": "bad",
               "detail": "Probe seal too poor to test. Refit the ear tip."}
        yield finish({"stage": "probe_fit", "reason": "Probe seal too poor to test. Refit the ear tip."})
        return
    yield {"event": "phase", "phase": "probe_fit", "status": "ok", "detail": fit["status"]}

    # 2 calibration
    yield {"event": "phase", "phase": "calibration", "status": "running"}
    cal = dsp.calibrate(source)
    quality["calibration"] = cal
    if not cal["ok"]:
        yield {"event": "phase", "phase": "calibration", "status": "bad", "detail": cal["reason"]}
        yield finish({"stage": "calibration", "reason": f"Calibration failed ({cal['reason']}). Refit the probe."})
        return
    yield {"event": "phase", "phase": "calibration", "status": "ok"}

    # 3 noise check
    yield {"event": "phase", "phase": "noise_check", "status": "running"}
    noise = dsp.noise_reference(source)
    quality["noise"] = {k: v for k, v in noise.items() if k != "residual_power"}
    if noise["expected_floor_db"] > noise["detectable_floor_db"]:
        msg = "Ambient noise too high to detect an emission reliably. Move somewhere quieter."
        yield {"event": "phase", "phase": "noise_check", "status": "bad", "detail": msg}
        yield finish({"stage": "noise_check", "reason": msg})
        return
    yield {"event": "phase", "phase": "noise_check", "status": "ok"}

    # 4 stimulus + 5 averaging
    yield {"event": "phase", "phase": "stimulus", "status": "running"}
    for i in range(len(dsp.PLAN)):
        for ev in measure_frequency(source, i, cal["gains_db"][i], noise["residual_power"],
                                    frames_per_freq, dsp.MAX_FRAMES_PER_FREQ, snapshot_every):
            if ev["event"] == "freq_result":
                per_freq.append(ev["result"])
            yield ev
    yield {"event": "phase", "phase": "stimulus", "status": "ok"}
    yield {"event": "phase", "phase": "averaging", "status": "ok",
           "detail": f"{sum(r['frames_valid'] for r in per_freq)}/{sum(r['frames_total'] for r in per_freq)} frames used"}

    # 6 decision
    quality["acquisition_s"] = round(sum(r["frames_total"] for r in per_freq) * dsp.NFFT / dsp.FS, 1)
    res = finish(None)
    yield {"event": "phase", "phase": "decision", "status": "ok" if res["verdict"] == "PASS" else "bad"}
    yield res


def run_ear(source, ear: str = "left", frames_per_freq: int = dsp.FRAMES_PER_FREQ) -> Dict:
    """Run a whole ear and return only the final result event."""
    last = None
    for ev in screen_ear_events(source, ear, frames_per_freq, snapshot_every=frames_per_freq):
        if ev["event"] == "result":
            last = ev
    return last
