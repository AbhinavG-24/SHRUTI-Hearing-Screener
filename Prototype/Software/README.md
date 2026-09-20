# Software — reference DSP, simulator and console

A Python implementation of SHRUTI's **screening pipeline** running against a **synthetic ear**, with a browser console on top. It lets the team test the signal-processing design and the PASS / REFER / RETEST logic **without hardware or infants**, and it is meant to be the reference the ESP32-S3 firmware is later checked against.

> Not a medical device. The ear, probe and noise are models. Nothing produced here is a measurement of a real device or infant.

## What is real and what is simulated

| Real (computed) | Simulated (model assumptions) |
| --- | --- |
| 48 kHz sampling, 8192-point FFT, bin-exact f₁ / f₂ / 2f₁−f₂ | The ear: emission level vs "outer hair cell function" |
| Phase-locked coherent averaging with A/B split | Probe seal, ambient noise, crying / movement interference |
| Frame rejection against an in-situ noise reference | Distortion of the two-receiver and shared-transducer rigs |
| Probe-fit check, per-frequency calibration, noise check | Everything the microphone "hears" |
| Per-frequency and per-ear decision rules, follow-up logic | |
| All console numbers (analytics, registry, self-test, study) | |

Every constant, and whether it comes from the project presentation or is a simulator assumption, is listed in [`Documentation/Technical-Documentation/Simulation-Model.md`](../../Documentation/Technical-Documentation/Simulation-Model.md).

## Run

Requires Python 3.10+ (developed and tested on 3.12).

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python server.py
```

Open `http://127.0.0.1:8000/`. API docs are at `/docs`.

The on-screen product name is set in `config.py` (`BRAND_NAME`).

## Tests

```powershell
pip install -r requirements-dev.txt
pytest
```

The suite (about 40 tests, roughly 25 s) checks the DSP against the design's numbers (bin width, exact bins, zero leakage, +13.6 dB from 23-frame averaging, about −18.9 dB noise floor), the safety behaviour (no PASS on a bad seal, loud room, sealed cavity or absent emission; the shared-transducer failure mode is reproduced), the clinical rules, and the API and WebSocket.

## Layout

| File | Purpose |
| --- | --- |
| `signal_processing.py` | Reference DSP: bin plan, FFT, averaging, calibration, frame rejection, detection. Pure NumPy, written to be portable to C/C++ |
| `ear_simulator.py` | Synthetic ear / probe / noise model that produces microphone frames |
| `screening.py` | Sequences the stages into a stream of events and calls the clinical rules |
| `clinical_engine.py` | Per-ear and overall decisions, follow-up scheduling, escalation ladder |
| `simulation_study.py` | Sealed-cavity trials, self-test, sweeps over noise / interference / hair-cell function |
| `server.py` | FastAPI REST + WebSocket server (in-memory state, local use) |
| `console.html` | Browser console |
| `config.py` | Brand name, version, protocol id |
| `tests/` | pytest suite |

## Limitations

- Synthetic data only; there is no hardware I/O and nothing is persisted.
- The ear model is not physiology (no middle-ear effects, vernix, spontaneous emissions, harmonic distortion or frequency-shaped noise).
- Thresholds marked as assumptions in `Simulation-Model.md` are placeholders until bench data exist.
- The simulated cohort contains an artificially high share of impaired ears; its rates are not prevalence.
- Follow-up timings other than the 6/10/14-week immunisation visits are placeholders.
- Intended for local use: no authentication, in-memory storage.

Screenshots: **To be added** (`Images/Screenshots/`).
