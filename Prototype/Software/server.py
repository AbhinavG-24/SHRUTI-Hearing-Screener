"""SHRUTI simulator server (FastAPI).

Serves the console and a small API. Every verdict is computed here by the reference DSP
running on a *synthetic* ear (see ear_simulator.py). There is no hardware connection, no
persistence (in-memory only) and no randomly invented statistics: analytics are derived
only from screenings that were actually run in this session.

Run:  python server.py   ->  http://127.0.0.1:8000/
"""
from __future__ import annotations

import asyncio
import random
import re
import threading
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Literal, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

import clinical_engine as clinical
import signal_processing as dsp
import simulation_study
from config import APP_VERSION, BRAND_NAME, PROTOCOL_VERSION, SIMULATION_NOTICE
from ear_simulator import ARTIFACTS, SimulatedEar
from screening import measure_frequency, screen_ear_events

HERE = Path(__file__).parent
app = FastAPI(title=f"{BRAND_NAME} simulator", version=APP_VERSION)

infants: Dict[str, Dict] = {}
follow_ups: Dict[str, Dict] = {}          # keyed by infant id: at most one open task per infant
_study_lock = threading.Lock()
_last_study: Optional[Dict] = None
_counter = {"infant": 0}

FACILITIES = ["Facility A", "Facility B", "Facility C", "Facility D"]   # generic labels, not real sites
DOB_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class Conditions(BaseModel):
    ohc: float = Field(100, ge=0, le=100, description="outer-hair-cell function, % (illustrative scale)")
    ambient_db: float = Field(38, ge=20, le=90)
    seal: float = Field(0.88, ge=0, le=1)
    rig: Literal["two", "one"] = "two"
    artifact: Literal["none", "crying", "movement"] = "none"
    seed: Optional[int] = None

    def ear(self) -> SimulatedEar:
        return SimulatedEar(ohc=self.ohc, ambient_db=self.ambient_db, seal=self.seal,
                            rig=self.rig, artifact=self.artifact, seed=self.seed)


class ScreeningRequest(BaseModel):
    infant_id: Optional[str] = None
    ear: Literal["left", "right"] = "left"
    conditions: Conditions = Conditions()


class InfantRegistration(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    dob: str
    facility: str = Field("Facility A", max_length=80)


class SimulateRequest(BaseModel):
    conditions: Conditions = Conditions()
    freq_index: int = Field(2, ge=0, le=3)
    avg_s: float = Field(4.0, ge=1, le=12)


class CohortRequest(BaseModel):
    n: int = Field(24, ge=1, le=60)


# ---------------------------------------------------------------------------
# Store helpers
# ---------------------------------------------------------------------------
def _new_infant(name: str, dob: str, facility: str, simulated: bool) -> Dict:
    _counter["infant"] += 1
    iid = f"I{_counter['infant']:04d}"
    infants[iid] = {"id": iid, "name": name, "dob": dob, "facility": facility, "simulated": simulated,
                    "registered": date.today().isoformat(), "screenings": []}
    return infants[iid]


def _latest_by_ear(inf: Dict) -> Dict[str, Dict]:
    out: Dict[str, Dict] = {}
    for s in inf["screenings"]:
        out[s["ear"]] = s
    return out


def _view(inf: Dict) -> Dict:
    latest = _latest_by_ear(inf)
    verdicts = {e: latest[e]["verdict"] for e in latest}
    overall = clinical.combine_ears(verdicts) if latest else "NOT_SCREENED"
    task = follow_ups.get(inf["id"])
    v = {k: inf[k] for k in ("id", "name", "dob", "facility", "simulated", "registered")}
    v.update({"left": verdicts.get("left"), "right": verdicts.get("right"), "overall": overall,
              "snr_left": latest["left"]["mean_snr_db"] if "left" in latest else None,
              "snr_right": latest["right"]["mean_snr_db"] if "right" in latest else None,
              "tests": len(inf["screenings"]),
              "follow_up": None if not task or task["status"] != "open" else task})
    return v


def _record(inf: Dict, ear: str, result: Dict) -> Dict:
    snrs = [f["snr_db"] for f in result["per_frequency"] if f["snr_db"] is not None]
    rec = {"id": f"S{uuid.uuid4().hex[:8]}", "infant_id": inf["id"], "infant_name": inf["name"], "ear": ear,
           "timestamp": datetime.now().isoformat(timespec="seconds"), "verdict": result["verdict"],
           "reason": result["reason"], "decision": result["decision"], "per_frequency": [
               {k: v for k, v in f.items() if k != "local_db"} for f in result["per_frequency"]],
           "quality": result["quality"], "conditions": result["conditions"],
           "mean_snr_db": round(sum(snrs) / len(snrs), 1) if snrs else None,
           "protocol_version": PROTOCOL_VERSION, "software_version": APP_VERSION,
           "source": "simulation", "note": clinical.DISCLAIMER}
    inf["screenings"].append(rec)
    _refresh_follow_up(inf)
    return rec


def _refresh_follow_up(inf: Dict) -> None:
    latest = _latest_by_ear(inf)
    overall = clinical.combine_ears({e: latest[e]["verdict"] for e in latest})
    existing = follow_ups.get(inf["id"])
    task = clinical.make_follow_up(inf, overall) if overall in ("REFER", "RETEST") else None
    if task:
        if existing and existing["status"] == "open" and existing["trigger"] == task["trigger"]:
            return
        follow_ups[inf["id"]] = task
    elif existing and existing["status"] == "open":
        existing["status"] = "closed"
        existing["closed_reason"] = "latest screening passed" if overall == "PASS" else "superseded"


def _run_blocking(req: ScreeningRequest) -> List[Dict]:
    return list(screen_ear_events(req.conditions.ear(), req.ear))


def _get_infant(iid: Optional[str]) -> Optional[Dict]:
    if iid is None:
        return None
    inf = infants.get(iid)
    if not inf:
        raise HTTPException(404, "Infant not found")
    return inf


# ---------------------------------------------------------------------------
# Pages and meta
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def console():
    html = (HERE / "console.html").read_text(encoding="utf-8")
    return html.replace("__BRAND__", BRAND_NAME).replace("__VERSION__", APP_VERSION)


@app.get("/api/health")
async def health():
    return {"status": "ok", "mode": "simulation", "version": APP_VERSION, "brand": BRAND_NAME,
            "notice": SIMULATION_NOTICE, "timestamp": datetime.now().isoformat(timespec="seconds")}


@app.get("/api/config")
async def get_config():
    return {
        "fs_hz": dsp.FS, "nfft": dsp.NFFT, "bin_hz": dsp.BIN_HZ,
        "plan": [{"label": p.label, "f1_hz": p.f1_hz, "f2_hz": p.f2_hz, "fdp_hz": p.fdp_hz,
                  "k1": p.k1, "k2": p.k2, "kdp": p.kdp} for p in dsp.PLAN],
        "levels_db_spl": {"l1": dsp.L1_DB, "l2": dsp.L2_DB},
        "criteria": {"snr_db": dsp.SNR_CRITERION_DB, "freqs_required": dsp.PASS_FREQS_REQUIRED,
                     "frames_per_freq": dsp.FRAMES_PER_FREQ, "max_frames_per_freq": dsp.MAX_FRAMES_PER_FREQ,
                     "detectable_floor_db": dsp.DETECTABLE_FLOOR_DB},
        "display_max_hz": dsp.DISPLAY_MAX_HZ, "artifacts": list(ARTIFACTS),
        "protocol_version": PROTOCOL_VERSION,
    }


# ---------------------------------------------------------------------------
# Infants
# ---------------------------------------------------------------------------
@app.get("/api/infants")
async def list_infants():
    return {"infants": [_view(i) for i in infants.values()]}


@app.post("/api/infants")
async def register_infant(reg: InfantRegistration):
    if not DOB_RE.match(reg.dob):
        raise HTTPException(422, "dob must be YYYY-MM-DD")
    try:
        born = datetime.strptime(reg.dob, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(422, "dob is not a valid date")
    if born > date.today():
        raise HTTPException(422, "dob cannot be in the future")
    return {"infant": _view(_new_infant(reg.name.strip(), reg.dob, reg.facility.strip(), False))}


@app.get("/api/infants/{iid}")
async def get_infant(iid: str):
    inf = _get_infant(iid)
    return {"infant": _view(inf), "screenings": inf["screenings"]}


@app.post("/api/reset")
async def reset():
    infants.clear()
    follow_ups.clear()
    _counter["infant"] = 0
    return {"status": "cleared"}


# ---------------------------------------------------------------------------
# Screening (all verdicts computed server-side)
# ---------------------------------------------------------------------------
@app.post("/api/screenings")
async def run_screening(req: ScreeningRequest):
    inf = _get_infant(req.infant_id)
    events = await asyncio.to_thread(_run_blocking, req)
    result = events[-1]
    rec = _record(inf, req.ear, result) if inf else None
    return {"result": {k: v for k, v in result.items() if k != "event"}, "screening": rec,
            "infant": _view(inf) if inf else None}


@app.websocket("/ws")
async def screening_ws(ws: WebSocket):
    """Client sends one ScreeningRequest JSON; server streams pipeline events, paced for display."""
    await ws.accept()
    try:
        raw = await ws.receive_json()
        try:
            req = ScreeningRequest(**raw)
            inf = _get_infant(req.infant_id)
        except Exception as exc:                                  # invalid input -> report, close
            await ws.send_json({"event": "error", "detail": str(getattr(exc, "detail", exc))[:300]})
            await ws.close()
            return
        events = await asyncio.to_thread(_run_blocking, req)
        def pause(e):
            if e["event"] == "phase":
                return 0.32 if e.get("status") == "running" else 0.12
            return {"spectrum": 0.10, "freq_result": 0.2}.get(e["event"], 0.0)
        for ev in events:
            if ev["event"] == "result":
                rec = _record(inf, req.ear, ev) if inf else None
                ev = {**ev, "screening": rec, "infant": _view(inf) if inf else None}
            await ws.send_json(ev)
            await asyncio.sleep(pause(ev))
        await ws.close()
    except WebSocketDisconnect:
        return


@app.post("/api/simulate")
async def simulate(req: SimulateRequest):
    """Fast what-if for one frequency pair (used by the Simulator tab)."""
    def work():
        ear = req.conditions.ear()
        plan = dsp.PLAN[req.freq_index]
        out: Dict = {"freq_index": req.freq_index, "label": plan.label, "f1_hz": plan.f1_hz,
                     "f2_hz": plan.f2_hz, "fdp_hz": plan.fdp_hz, "stage_failure": None}
        fit = dsp.probe_fit(ear, dsp.PLAN[2])
        if fit["status"] == "reject":
            return {**out, "stage_failure": "Probe seal too poor to test."}
        cal = dsp.calibrate(ear)
        if not cal["ok"]:
            return {**out, "stage_failure": f"Calibration failed ({cal['reason']})."}
        noise = dsp.noise_reference(ear)
        if noise["expected_floor_db"] > noise["detectable_floor_db"]:
            return {**out, "stage_failure": "Ambient noise too high to detect an emission reliably.",
                    "noise": {k: v for k, v in noise.items() if k != "residual_power"}}
        target = max(2, round(req.avg_s * dsp.FS / dsp.NFFT))
        last, final = None, None
        for ev in measure_frequency(ear, req.freq_index, cal["gains_db"][req.freq_index],
                                    noise["residual_power"], target, target * 2, None):
            if ev["event"] == "spectrum":
                last = ev
            else:
                final = ev["result"]
        return {**out, "spec_db": last["spec_db"] if last else None, "result": {
            k: v for k, v in (final or {}).items() if k != "local_db"}, "frames_target": target}
    return await asyncio.to_thread(work)


# ---------------------------------------------------------------------------
# Simulated cohort (exercises the same pipeline; NOT a representative population)
# ---------------------------------------------------------------------------
def _random_conditions(rng: random.Random, ohc: float) -> Conditions:
    artifact = rng.choices(["none", "crying", "movement"], [0.80, 0.12, 0.08])[0]
    ambient = rng.choice([rng.uniform(30, 55)] * 9 + [rng.uniform(64, 72)])
    seal = rng.choice([rng.uniform(0.62, 0.96)] * 9 + [rng.uniform(0.25, 0.45)])
    return Conditions(ohc=ohc, ambient_db=round(ambient, 1), seal=round(seal, 2), artifact=artifact,
                      seed=rng.randrange(1 << 30))


def _build_cohort(n: int) -> List[str]:
    rng = random.Random()
    made: List[str] = []
    for _ in range(n):
        inf = _new_infant(f"Simulated infant {_counter['infant'] + 1:03d}",
                          (date.today() - timedelta(days=rng.randint(1, 120))).isoformat(),
                          rng.choice(FACILITIES), True)
        made.append(inf["id"])
        kind = rng.choices(["healthy", "unilateral", "bilateral", "mild"], [0.72, 0.08, 0.12, 0.08])[0]
        ohc = {"healthy": (rng.uniform(85, 100), rng.uniform(85, 100)),
               "unilateral": (rng.uniform(0, 30), rng.uniform(85, 100)),
               "bilateral": (rng.uniform(0, 30), rng.uniform(0, 30)),
               "mild": (rng.uniform(50, 70), rng.uniform(50, 70))}[kind]
        for ear, o in zip(("left", "right"), ohc):
            ev = list(screen_ear_events(_random_conditions(rng, o).ear(), ear, snapshot_every=dsp.MAX_FRAMES_PER_FREQ))
            res = ev[-1]
            _record(inf, ear, res)
    return made


@app.post("/api/simulated-cohort")
async def simulated_cohort(req: CohortRequest):
    ids = await asyncio.to_thread(_build_cohort, req.n)
    return {"created": len(ids), "note": "Simulated infants with an artificially high proportion of impaired "
            "ears; not representative of any real population or of prevalence."}


# ---------------------------------------------------------------------------
# Follow-up and analytics (derived from this session's records only)
# ---------------------------------------------------------------------------
@app.get("/api/follow-ups")
async def list_follow_ups():
    rows = []
    for t in follow_ups.values():
        rows.append({**t, **clinical.escalation_stage(t["due"])} if t["status"] == "open" else t)
    rows.sort(key=lambda r: r["due"])
    return {"follow_ups": rows}


@app.get("/api/analytics")
async def analytics():
    views = [_view(i) for i in infants.values()]
    tests = [s for i in infants.values() for s in i["screenings"]]
    def count(seq, key, val):
        return sum(1 for x in seq if x[key] == val)
    by_facility: Dict[str, Dict[str, int]] = {}
    for v in views:
        f = by_facility.setdefault(v["facility"], {"registered": 0, "screened": 0})
        f["registered"] += 1
        f["screened"] += 1 if v["overall"] != "NOT_SCREENED" else 0
    by_day: Dict[str, int] = {}
    for s in tests:
        d = s["timestamp"][:10]
        by_day[d] = by_day.get(d, 0) + 1
    n_tests = len(tests)
    rate = lambda c: round(100 * c / n_tests, 1) if n_tests else None
    return {
        "simulated": True,
        "infants_registered": len(views),
        "infants_by_status": {s: sum(1 for v in views if v["overall"] == s)
                              for s in ("PASS", "REFER", "RETEST", "INCOMPLETE", "NOT_SCREENED")},
        "ear_tests": n_tests,
        "pass": count(tests, "verdict", "PASS"), "refer": count(tests, "verdict", "REFER"),
        "retest": count(tests, "verdict", "RETEST"),
        "pass_rate": rate(count(tests, "verdict", "PASS")), "refer_rate": rate(count(tests, "verdict", "REFER")),
        "retest_rate": rate(count(tests, "verdict", "RETEST")),
        "open_follow_ups": sum(1 for t in follow_ups.values() if t["status"] == "open"),
        "by_facility": by_facility,
        "tests_by_day": [{"date": d, "tests": c} for d, c in sorted(by_day.items())],
    }


# ---------------------------------------------------------------------------
# Simulated device self-test and simulation study
# ---------------------------------------------------------------------------
@app.get("/api/device/self-test")
async def device_self_test(rig: Literal["two", "one"] = "two"):
    return {"unit": "simulated", **await asyncio.to_thread(simulation_study.self_test, rig)}


VALIDATION_PLAN = [
    {"tier": 1, "scope": "Bench (no ethics approval needed)", "criteria": [
        "System 2f1-f2 <= -15 dB SPL",
        "<= 1 PASS in 120 repetitions into sealed 0.05 / 0.1 / 0.2 mL cavities"], "status": "not_run",
     "evidence": None},
    {"tier": 2, "scope": "About 30 normal-hearing adults", "criteria": [
        "PASS/REFER agreement against a clinical ERO-SCAN / Titan, reported as Cohen's kappa",
        "Bland-Altman analysis on DP amplitude"], "status": "not_run", "evidence": None},
    {"tier": 3, "scope": "Neonatal", "criteria": [
        "Protocol written and ethics-committee (IEC) submission prepared with a clinical partner"],
     "status": "not_claimed", "evidence": None},
]


@app.get("/api/validation")
async def validation():
    return {"plan": VALIDATION_PLAN, "last_study": _last_study,
            "statement": "No bench, adult-volunteer or neonatal results exist in this project yet. The "
                         "simulation study below tests the software, not the device."}


@app.post("/api/validation/simulation-study")
async def run_simulation_study(n: int = 20):
    global _last_study
    if not _study_lock.acquire(blocking=False):
        raise HTTPException(409, "A simulation study is already running")
    try:
        _last_study = await asyncio.to_thread(simulation_study.run_study, max(5, min(n, 60)))
        _last_study["generated"] = datetime.now().isoformat(timespec="seconds")
        return _last_study
    finally:
        _study_lock.release()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
