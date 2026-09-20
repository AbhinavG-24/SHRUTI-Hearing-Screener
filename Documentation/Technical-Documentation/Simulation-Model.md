# Simulation model: what comes from the design, what is assumed

The simulator in [`Prototype/Software`](../../Prototype/Software/) is only as meaningful as its inputs. This page separates numbers taken from the project presentation from choices made in software. Replace assumptions with bench measurements as they become available.

## From the presentation

| Item | Value |
| --- | --- |
| Sampling rate / FFT | 48 kHz / 8192 points (5.859375 Hz per bin) |
| Test frequencies | f₂ = 2, 3, 4, 5 kHz; f₂/f₁ = 1.22; L₁/L₂ = 65/55 dB SPL |
| Bin-exact stimulus | f₁, f₂ and 2f₁−f₂ on exact FFT bin centres |
| Averaging | 23 frames (≈ 4 s), +13.6 dB |
| Microphone self-noise | 27 dB(A) → about −5.3 dB SPL in a 5.86 Hz bin |
| Expected floor after averaging | about −18.9 dB SPL |
| Target emission / criterion | about 0 dB SPL, 6 dB SNR |
| PASS rule | SNR ≥ 6 dB at ≥ 3 of 4 frequencies (from the concept video; the slides state the 6 dB criterion) |
| Distortion limit (Tier 1) | 2f₁−f₂ ≤ −15 dB SPL |
| False-PASS criterion (Tier 1) | ≤ 1 PASS in 120 repetitions into sealed cavities |
| Immunisation visits | 6 / 10 / 14 weeks |

## Assumptions made in software (not from the presentation)

| Item | Value | Where |
| --- | --- | --- |
| Minimum clean frames per frequency | 12 | `signal_processing.py` |
| Maximum frames per frequency (acquire until 23 clean) | 46 | `signal_processing.py` |
| A/B repeatability: each half must show | ≥ 3 dB SNR | `signal_processing.py` |
| Frame rejection threshold | 1.5 × in-situ noise reference | `signal_processing.py` |
| "Testable" noise floor | ≤ −12 dB SPL (emission 0 dB, minus 6 dB criterion, minus 6 dB margin) | `signal_processing.py` |
| Probe-fit thresholds | level deficit ≤ 6 dB good, ≤ 15 dB marginal, above that refuse | `signal_processing.py` |
| Emission vs hair-cell function | 0 dB SPL at 100 %, −0.3 dB per % lost, ±2 dB per-frequency spread | `ear_simulator.py` |
| Seal → stimulus level loss | 30 dB × (1 − seal) | `ear_simulator.py` |
| Ambient noise in the canal | ambient dB(A) − 10·log₁₀(3000 Hz / bin) − (12 + 28 × seal) dB | `ear_simulator.py` |
| Crying / movement | 35 % / 15 % of frames hit, +20 / +25 dB per-bin noise | `ear_simulator.py` |
| Two-receiver distortion | −22 dB SPL | `ear_simulator.py` |
| Shared-transducer distortion | +4 dB SPL | `ear_simulator.py` |
| Overall precedence | REFER over RETEST over PASS | `clinical_engine.py` |
| RETEST delay | 1 day | `clinical_engine.py` |
| Escalation ladder | 1 / 14 / 28 days → SMS / ASHA home visit / block worklist | `clinical_engine.py` |

## What the model leaves out

Middle-ear and canal-volume effects on the emission, neonatal vernix and debris, spontaneous otoacoustic emissions, harmonic and other distortion products, frequency-shaped ambient noise, microphone / codec quantisation and anti-alias filtering, drift of calibration during a test, and any timing behaviour of the real firmware.
