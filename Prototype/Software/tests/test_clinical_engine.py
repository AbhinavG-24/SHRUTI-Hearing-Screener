from datetime import date

import clinical_engine as ce


def freq(outcome):
    return {"outcome": outcome}


def test_pass_needs_three_of_four():
    assert ce.decide_ear([freq("present")] * 3 + [freq("absent")])["verdict"] == "PASS"
    assert ce.decide_ear([freq("present")] * 2 + [freq("absent")] * 2)["verdict"] == "REFER"


def test_indeterminate_frequencies_cannot_produce_pass_or_refer():
    d = ce.decide_ear([freq("present"), freq("present"), freq("indeterminate"), freq("absent")])
    assert d["verdict"] == "RETEST"


def test_stage_failure_is_retest():
    d = ce.decide_ear([], {"stage": "probe_fit", "reason": "seal"})
    assert d["verdict"] == "RETEST" and d["stage_failure"] == "probe_fit"


def test_overall_precedence_refer_over_retest_over_pass():
    assert ce.combine_ears({"left": "PASS", "right": "PASS"}) == "PASS"
    assert ce.combine_ears({"left": "PASS", "right": "RETEST"}) == "RETEST"
    assert ce.combine_ears({"left": "REFER", "right": "RETEST"}) == "REFER"
    assert ce.combine_ears({"left": "PASS"}) == "INCOMPLETE"
    assert ce.combine_ears({"left": "REFER"}) == "REFER"


def test_refer_follow_up_uses_next_immunisation_visit():
    today = date(2026, 9, 1)
    assert ce.next_immunisation_visit("2026-08-01", today).isoformat() == "2026-09-12"   # 6 weeks
    assert ce.next_immunisation_visit("2026-07-01", today).isoformat() == "2026-09-09"   # 10 weeks
    assert ce.next_immunisation_visit("2026-01-01", today) == date(2026, 9, 8)            # past 14 wk -> +7 d


def test_pass_creates_no_follow_up():
    assert ce.make_follow_up({"id": "I1", "name": "x", "dob": "2026-08-01"}, "PASS") is None


def test_escalation_ladder():
    today = date(2026, 9, 30)
    assert ce.escalation_stage("2026-10-05", today)["stage"] == "Not yet due"
    assert ce.escalation_stage("2026-09-29", today)["stage"] == "SMS reminder"
    assert ce.escalation_stage("2026-09-10", today)["stage"] == "ASHA home visit"
    assert ce.escalation_stage("2026-08-20", today)["stage"] == "Block worklist"
