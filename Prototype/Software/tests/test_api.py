import pytest
from fastapi.testclient import TestClient

import server
from config import BRAND_NAME


@pytest.fixture()
def client():
    c = TestClient(server.app)
    c.post("/api/reset")
    return c


def test_health_and_brand(client):
    assert client.get("/api/health").json()["mode"] == "simulation"
    html = client.get("/").text
    assert BRAND_NAME in html and "__BRAND__" not in html
    assert "NEHA" not in html.upper()


def test_verdict_is_computed_server_side_not_supplied_by_client(client):
    iid = client.post("/api/infants", json={"name": "Test infant", "dob": "2026-09-01"}).json()["infant"]["id"]
    r = client.post("/api/screenings", json={"infant_id": iid, "ear": "left",
                    "conditions": {"ohc": 0, "seed": 1, "verdict": "PASS"}}).json()
    assert r["result"]["verdict"] == "REFER"
    assert r["infant"]["left"] == "REFER" and r["infant"]["overall"] == "REFER"
    assert r["infant"]["follow_up"]["trigger"] == "REFER"


def test_both_ears_pass_closes_follow_up(client):
    iid = client.post("/api/infants", json={"name": "A", "dob": "2026-09-01"}).json()["infant"]["id"]
    client.post("/api/screenings", json={"infant_id": iid, "ear": "left", "conditions": {"ohc": 0, "seed": 2}})
    for ear in ("left", "right"):
        r = client.post("/api/screenings", json={"infant_id": iid, "ear": ear, "conditions": {"seed": 3}}).json()
    assert r["infant"]["overall"] == "PASS" and r["infant"]["follow_up"] is None
    assert client.get("/api/follow-ups").json()["follow_ups"][0]["status"] == "closed"


def test_registration_validation(client):
    assert client.post("/api/infants", json={"name": "x", "dob": "01-09-2026"}).status_code == 422
    assert client.post("/api/infants", json={"name": "x", "dob": "2999-01-01"}).status_code == 422
    assert client.post("/api/screenings", json={"infant_id": "nope"}).status_code == 404
    assert client.post("/api/screenings", json={"conditions": {"ohc": 500}}).status_code == 422


def test_analytics_are_empty_when_nothing_was_run(client):
    a = client.get("/api/analytics").json()
    assert a["ear_tests"] == 0 and a["pass_rate"] is None and a["by_facility"] == {}


def test_analytics_match_the_records(client):
    client.post("/api/simulated-cohort", json={"n": 4})
    a = client.get("/api/analytics").json()
    infants = client.get("/api/infants").json()["infants"]
    assert a["infants_registered"] == 4 == len(infants)
    assert a["ear_tests"] == 8 and a["pass"] + a["refer"] + a["retest"] == 8
    assert all(i["simulated"] for i in infants)


def test_websocket_streams_pipeline_events(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"ear": "right", "conditions": {"ohc": 100, "seed": 5}})
        kinds = []
        while True:
            try:
                ev = ws.receive_json()
            except Exception:
                break
            kinds.append(ev["event"])
            if ev["event"] == "result":
                assert ev["verdict"] == "PASS" and ev["ear"] == "right"
                break
    assert kinds[0] == "phase" and "spectrum" in kinds and kinds.count("freq_result") == 4


def test_websocket_rejects_bad_input(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"conditions": {"ohc": 999}})
        assert ws.receive_json()["event"] == "error"


def test_simulate_endpoint(client):
    ok = client.post("/api/simulate", json={"conditions": {"ohc": 100, "seed": 1}}).json()
    assert ok["result"]["outcome"] == "present" and len(ok["spec_db"]) > 1000
    bad = client.post("/api/simulate", json={"conditions": {"seal": 0.2}}).json()
    assert bad["stage_failure"]


def test_validation_makes_no_measured_claims(client):
    v = client.get("/api/validation").json()
    assert all(t["evidence"] is None for t in v["plan"])
    assert {t["status"] for t in v["plan"]} <= {"not_run", "not_claimed"}


def test_self_test_locks_out_the_shared_transducer_rig(client):
    assert client.get("/api/device/self-test?rig=two").json()["in_service"] is True
    assert client.get("/api/device/self-test?rig=one").json()["in_service"] is False
