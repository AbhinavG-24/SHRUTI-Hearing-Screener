"""End-to-end behaviour of the screening pipeline on simulated ears.

Statistical tests use fixed seeds so they are reproducible. They verify software behaviour
against the design's safety properties, not device performance.
"""
from collections import Counter

import pytest

from ear_simulator import SimulatedEar
from screening import run_ear, screen_ear_events


def verdicts(n, **kw):
    return Counter(run_ear(SimulatedEar(seed=100 + i, **kw))["verdict"] for i in range(n))


def test_healthy_ear_passes():
    assert verdicts(30, ohc=100)["PASS"] == 30


def test_absent_emission_is_referred_never_passed():
    c = verdicts(150, ohc=0, seal=1.0, ambient_db=30)
    assert c["PASS"] == 0 and c["REFER"] == 150     # design goal: <= 1 false PASS in 120


def test_sealed_cavity_false_pass_criterion():
    c = verdicts(120, ohc=0, seal=1.0, ambient_db=30)
    assert c["PASS"] <= 1


def test_single_shared_transducer_falsely_passes_a_deaf_ear():
    """The failure mode the two-receiver design exists to prevent."""
    assert verdicts(20, ohc=0, rig="one")["PASS"] >= 18


def test_bad_seal_gives_retest_not_pass_or_refer():
    r = run_ear(SimulatedEar(seal=0.3, seed=1))
    assert r["verdict"] == "RETEST" and r["decision"]["stage_failure"] == "probe_fit"


@pytest.mark.parametrize("ambient", [65, 70, 80])
def test_loud_room_gives_retest_for_healthy_and_deaf_ears(ambient):
    for ohc in (100, 0):
        assert run_ear(SimulatedEar(ohc=ohc, ambient_db=ambient, seed=2))["verdict"] == "RETEST"


@pytest.mark.parametrize("artifact", ["crying", "movement"])
def test_artifacts_never_turn_a_healthy_ear_into_a_refer(artifact):
    c = verdicts(30, ohc=100, artifact=artifact)
    assert c["REFER"] == 0


def test_rejected_frames_are_reported():
    r = run_ear(SimulatedEar(artifact="crying", seed=4))
    assert any(f["frames_total"] > f["frames_valid"] for f in r["per_frequency"])


def test_clean_test_takes_under_30_seconds_of_acquisition():
    r = run_ear(SimulatedEar(seed=6))
    assert r["quality"]["acquisition_s"] < 30


def test_event_stream_order():
    events = list(screen_ear_events(SimulatedEar(seed=8), "left"))
    kinds = [e["event"] for e in events]
    assert kinds[0] == "phase" and kinds[-1] == "result"
    assert kinds.count("freq_result") == 4
    assert events[-1]["decision"]["verdict"] == events[-1]["verdict"]


def test_partial_hearing_loss_is_a_dose_response():
    hi = verdicts(20, ohc=90)["PASS"]
    lo = verdicts(20, ohc=30)["PASS"]
    assert hi > lo
