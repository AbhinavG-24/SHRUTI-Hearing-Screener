"""Decision rules and follow-up logic.

Rule set (simulator defaults, to be confirmed with a clinical partner):

  per frequency : emission present / absent / indeterminate   (see signal_processing)
  per ear       : PASS     emission present at >= 3 of 4 frequencies
                  RETEST   otherwise, if present + indeterminate >= 3 (cannot rule out) or a
                           pre-test stage failed (seal, calibration, noise)
                  REFER    otherwise (a clean measurement showed no emission)
  overall       : REFER if either ear is REFER, else RETEST if either ear is RETEST, else PASS

The rules never PASS on a bad seal, excess noise or too few clean frames, and never
diagnose: every result carries the screening-not-diagnosis statement.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from signal_processing import PASS_FREQS_REQUIRED

DISCLAIMER = "Screening result, not a diagnosis."
IMMUNISATION_VISIT_WEEKS = (6, 10, 14)      # from the presentation
RETEST_DELAY_DAYS = 1                       # placeholder: repeat as soon as conditions allow
ESCALATION = ((28, "Block worklist"), (14, "ASHA home visit"), (1, "SMS reminder"))  # placeholder days


def decide_ear(per_frequency: List[Dict], failure: Optional[Dict] = None) -> Dict:
    if failure:
        return {"verdict": "RETEST", "reason": failure["reason"], "n_present": 0, "n_absent": 0,
                "n_indeterminate": 0, "stage_failure": failure["stage"]}
    present = sum(1 for r in per_frequency if r["outcome"] == "present")
    absent = sum(1 for r in per_frequency if r["outcome"] == "absent")
    indet = sum(1 for r in per_frequency if r["outcome"] == "indeterminate")
    if present >= PASS_FREQS_REQUIRED:
        verdict, reason = "PASS", None
    elif present + indet >= PASS_FREQS_REQUIRED:
        verdict = "RETEST"
        reason = "Some frequencies could not be judged (noise or too few clean frames). Repeat the test."
    else:
        verdict, reason = "REFER", None
    return {"verdict": verdict, "reason": reason, "n_present": present, "n_absent": absent,
            "n_indeterminate": indet, "stage_failure": None}


def combine_ears(verdicts: Dict[str, str]) -> str:
    """Overall infant status from the latest verdict per ear. Missing ear -> INCOMPLETE."""
    vals = [verdicts.get("left"), verdicts.get("right")]
    if any(v == "REFER" for v in vals):
        return "REFER"
    if any(v is None for v in vals):
        return "INCOMPLETE"
    if any(v == "RETEST" for v in vals):
        return "RETEST"
    return "PASS"


def next_immunisation_visit(dob: str, today: Optional[date] = None) -> date:
    today = today or date.today()
    born = datetime.strptime(dob, "%Y-%m-%d").date()
    for w in IMMUNISATION_VISIT_WEEKS:
        d = born + timedelta(weeks=w)
        if d >= today:
            return d
    return today + timedelta(days=7)


def make_follow_up(infant: Dict, overall: str, today: Optional[date] = None) -> Optional[Dict]:
    today = today or date.today()
    if overall == "REFER":
        due = next_immunisation_visit(infant["dob"], today)
        action = "Re-screen at next immunisation visit; ABR referral if REFER persists"
    elif overall == "RETEST":
        due = today + timedelta(days=RETEST_DELAY_DAYS)
        action = "Repeat screening"
    else:
        return None
    return {"infant_id": infant["id"], "infant_name": infant["name"], "trigger": overall,
            "action": action, "due": due.isoformat(), "created": today.isoformat(), "status": "open"}


def escalation_stage(due: str, today: Optional[date] = None) -> Dict:
    today = today or date.today()
    days = (today - datetime.strptime(due, "%Y-%m-%d").date()).days
    stage = "Not yet due" if days <= 0 else "Due"
    for threshold, name in ESCALATION:
        if days >= threshold:
            stage = name
            break
    return {"days_overdue": max(0, days), "stage": stage}
