"""Verifies the reference DSP against the numbers in the design (presentation)."""
import numpy as np
import pytest

import signal_processing as dsp
from ear_simulator import SimulatedEar, MIC_NOISE_BIN_DB


def test_fft_bin_width():
    assert dsp.BIN_HZ == pytest.approx(5.859375)
    assert dsp.FS == 48_000 and dsp.NFFT == 8192


def test_every_frequency_is_an_exact_bin_and_dp_is_2f1_minus_f2():
    for p in dsp.PLAN:
        assert p.kdp == 2 * p.k1 - p.k2
        assert p.fdp_hz == pytest.approx(2 * p.f1_hz - p.f2_hz)
        assert p.f2_hz / p.f1_hz == pytest.approx(1.22, abs=0.01)


def test_presentation_example_pair_at_4khz():
    p = dsp.PLAN[2]
    # presentation shows f1 = 3281 Hz, f2 = 4002 Hz, 2f1-f2 = 2561 Hz
    assert round(p.f1_hz) == 3281 and round(p.f2_hz) == 4002 and round(p.fdp_hz) == 2561


def test_primaries_do_not_leak_into_the_emission_bin():
    """65 dB SPL primary must leave the emission bin empty (rectangular window, bin-exact)."""
    ear = SimulatedEar(ohc=0, ambient_db=20, seal=1.0, seed=0)
    ear.rng = np.random.default_rng(0)
    p = dsp.PLAN[2]
    n = np.arange(dsp.NFFT)
    tone = np.sqrt(2) * dsp.db_spl_to_pa_rms(65) * np.sin(2 * np.pi * p.k1 * n / dsp.NFFT + 0.7)
    power = dsp.bin_power(np.fft.rfft(tone))
    assert dsp.power_to_db_spl(power[p.k1]) == pytest.approx(65.0, abs=1e-6)
    assert dsp.power_to_db_spl(power[p.kdp]) < -100      # nothing where the emission would be


def test_coherent_averaging_lowers_noise_by_10log10_M():
    ear = SimulatedEar(ambient_db=20, seal=1.0, seed=3)
    X = dsp.frames_to_spectra(ear.capture_silence(230))
    single = dsp.power_to_db_spl(dsp.bin_power(X[:, 300:900]).mean())
    avg23 = dsp.power_to_db_spl(dsp.bin_power(X[:23].mean(axis=0))[300:900].mean())
    assert single == pytest.approx(MIC_NOISE_BIN_DB, abs=0.4)
    assert single - avg23 == pytest.approx(10 * np.log10(23), abs=0.6)       # +13.6 dB


def test_noise_floor_after_4s_matches_presentation_budget():
    """Presentation: floor = about -18.9 dB SPL after 23 frames."""
    r = dsp.analyse_frequency(dsp.frames_to_spectra(
        SimulatedEar(ohc=0, ambient_db=20, seal=1.0, seed=5).capture_stimulus(2, 23, (0, 0))), dsp.PLAN[2], 23)
    assert r["noise_db"] == pytest.approx(-18.9, abs=1.2)


def test_frame_duration_matches_about_four_seconds():
    assert dsp.FRAMES_PER_FREQ * dsp.NFFT / dsp.FS == pytest.approx(3.93, abs=0.01)


def test_emission_detected_and_absent_cases():
    healthy = SimulatedEar(ohc=100, seed=11)
    deaf = SimulatedEar(ohc=0, seed=12)
    for ear, expected in ((healthy, "present"), (deaf, "absent")):
        cal = dsp.calibrate(ear)
        base = dsp.noise_reference(ear)
        spectra = dsp.frames_to_spectra(ear.capture_stimulus(2, 23, cal["gains_db"][2]))
        ok = dsp.valid_frame_mask(spectra, base["residual_power"])
        assert dsp.analyse_frequency(spectra[ok], dsp.PLAN[2], 23)["outcome"] == expected


def test_ab_repeatability_rejects_a_one_sided_spike():
    """A transient that lands in only one A/B half must not be accepted as an emission."""
    ear = SimulatedEar(ohc=0, ambient_db=20, seal=1.0, seed=21)
    spectra = dsp.frames_to_spectra(ear.capture_silence(22))
    plan = dsp.PLAN[2]
    ref = dsp.analyse_frequency(spectra, plan, 22)
    assert ref["outcome"] == "absent"
    for mag in np.geomspace(1e-3, 50, 60):               # grow the spike until the pooled SNR passes 6 dB
        s = spectra.copy()
        s[0, plan.kdp] += mag * dsp.NFFT                 # only frame 0 (A half) carries it
        r = dsp.analyse_frequency(s, plan, 22)
        if r["snr_db"] >= dsp.SNR_CRITERION_DB:
            assert r["snr_b_db"] < dsp.AB_MIN_SNR_DB
            assert r["outcome"] != "present"
            return
    pytest.fail("spike never reached the pooled criterion")
