# Testing and validation

**Status: no test results are included in this repository. SHRUTI has not been clinically validated.**

This page records the acceptance criteria defined in advance in the project presentation, so that future results can be reported against them.

| Tier | Scope | Acceptance criteria | Status |
| --- | --- | --- | --- |
| 1 | Bench — no ethics approval needed | System 2f₁ − f₂ ≤ −15 dB SPL, and ≤ 1 PASS in 120 repetitions into sealed 0.05 / 0.1 / 0.2 mL cavities | Results to be added |
| 2 | ≈ 30 normal-hearing adults | PASS/REFER agreement against a clinical ERO·SCAN / Titan, reported as Cohen's kappa plus Bland–Altman analysis on DP amplitude | Not started / to be added |
| 3 | Neonatal | Protocol written and IEC (ethics committee) submission prepared with a clinical partner, per the presentation; no newborn data claimed | Not claimed |

## Design-stage noise budget (calculation, not a measurement)

- Mic self-noise 27 dB(A) → about −13.0 dB SPL/√Hz → about −5.3 dB in a 5.86 Hz FFT bin
- After 4 s of coherent averaging (23 frames, +13.6 dB): floor ≈ −18.9 dB SPL
- Target emission ≈ 0 dB SPL, criterion = 6 dB SNR → about 13 dB of margin

## Software verification (not device evidence)

The reference DSP and decision logic in `Prototype/Software` are covered by an automated suite (`pytest`) and a live **simulation study** (Validation tab of the console). They check that the *software* implements the design — for example that a sealed simulated cavity never yields a PASS, that a bad seal or loud room gives RETEST, and that a single shared transducer would falsely pass a deaf ear. They use a synthetic ear model, so they say nothing about the performance of the physical device and must not be cited as Tier 1, 2 or 3 evidence. Assumptions are listed in [`../Technical-Documentation/Simulation-Model.md`](../Technical-Documentation/Simulation-Model.md).

## Results

**To be added** — raw data, analysis scripts, calibration records, test setup description and date/firmware version for each run. Do not commit any data that could identify a person.
