import json
from pathlib import Path


def test_capsule_flow(client):
    first_status = client.get("/status").get_json()
    second_status = client.get("/status").get_json()
    assert second_status["api_calls"] == first_status["api_calls"] + 1

    payload = {"text": "Launch a community solar project", "tag": "solar"}
    save_resp = client.post("/capsules/save", json=payload)
    assert save_resp.status_code == 201
    capsule_id = save_resp.get_json()["id"]

    list_resp = client.get("/capsules/list")
    assert list_resp.status_code == 200
    capsules = list_resp.get_json()["capsules"]
    assert any(cap["id"] == capsule_id for cap in capsules)

    analyze_resp = client.post("/capsules/analyze", json={"text": payload["text"]})
    assert analyze_resp.status_code == 200
    analysis = analyze_resp.get_json()
    assert analysis["length"] > 0
    assert 0 <= analysis["ethics_score"] <= 1

    simulate_resp = client.post("/simulate/run", json={"text": payload["text"]})
    assert simulate_resp.status_code == 200
    simulation = simulate_resp.get_json()
    assert "plan" in simulation and simulation["plan"]

    learning_resp = client.post(
        "/learning/step",
        json={"engagement": 7.5, "success_rate": 0.9, "feedback_score": 0.8},
    )
    assert learning_resp.status_code == 200

    export_resp = client.get("/export/capsules?fmt=json")
    assert export_resp.status_code == 200
    exported = json.loads(export_resp.data.decode("utf-8"))
    assert isinstance(exported, list) and any(item["id"] == capsule_id for item in exported)

    logs_resp = client.get("/export/logs")
    assert logs_resp.status_code == 200

    status_resp = client.get("/status")
    status = status_resp.get_json()
    assert status["api_calls"] >= second_status["api_calls"] + 4
    assert status["capsules_processed"] >= 1
    assert status["last_action"]
    assert status["learning_version"].startswith("gaia-v")
    assert isinstance(status["learning_history"], list)
    assert status["learning_history"]

    history_resp = client.get("/learning/history")
    assert history_resp.status_code == 200
    history = history_resp.get_json()["history"]
    assert isinstance(history, list)
    assert history

    insights_resp = client.get("/insights/reflect")
    assert insights_resp.status_code == 200
    insights = insights_resp.get_json()
    assert insights["metadata"]["capsules"] >= 1
    assert isinstance(insights["insights"], list)

    data_root: Path = client.data_root
    activity = data_root / "logs" / "activity.jsonl"
    assert activity.exists()
    events = [json.loads(line)["event"] for line in activity.read_text().splitlines() if line.strip()]
    for event in ["status", "capsule_saved", "capsules_list", "capsule_analyze", "simulate", "export_capsules"]:
        assert event in events


def test_kill_switch_persists_and_blocks(client):
    denied = client.post("/admin/kill")
    assert denied.status_code == 403
    assert denied.get_json()["error"] == "Invalid admin token."

    kill_resp = client.post("/admin/kill", headers={"X-ADMIN-TOKEN": "test-token"})
    assert kill_resp.status_code == 200
    assert kill_resp.get_json()["status"] == "HALTED"

    status_resp = client.get("/status")
    status = status_resp.get_json()
    assert status["halted"] is True

    simulate_blocked = client.post("/simulate/run", json={"text": "Assess community gardens"})
    assert simulate_blocked.status_code == 423
    upgrade_blocked = client.post("/upgrades/propose", json={"proposal": "Test upgrade", "rationale": "safe"})
    assert upgrade_blocked.status_code == 423

    new_client = client.reload()
    restarted_status = new_client.get("/status").get_json()
    assert restarted_status["halted"] is True

    data_root: Path = client.data_root
    activity = data_root / "logs" / "activity.jsonl"
    kill_events = [json.loads(line) for line in activity.read_text().splitlines() if "kill_switch" in line]
    assert any(entry.get("event") == "kill_switch" for entry in kill_events)


def test_upgrade_ledger_records(client):
    payload = {"proposal": "Deploy transparent solar panels", "rationale": "Boost clean energy"}
    response = client.post("/upgrades/propose", json=payload)
    assert response.status_code == 200
    data = response.get_json()
    assert data["accepted"] is True
    assert data["decision"] == "accepted"

    data_root: Path = client.data_root
    ledger = data_root / "upgrades_ledger.jsonl"
    assert ledger.exists()
    lines = [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]
    assert any(entry.get("proposal") == payload["proposal"] for entry in lines)
    assert all("ethics_score" in entry for entry in lines)


def test_research_explore_route(client, monkeypatch):
    calls = {"count": 0}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            calls["count"] += 1
            return {
                "message": {
                    "items": [
                        {
                            "title": ["Sustainable test insight"],
                            "URL": "https://example.com/test",
                        }
                    ]
                }
            }

    def fake_get(*args, **kwargs):  # noqa: ANN001, ANN002
        return FakeResponse()

    monkeypatch.setattr("gaia.gaia_core.research.requests.get", fake_get)
    resp = client.get("/research/explore?q=solar")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["results"]
    assert payload["source"] == "crossref"
    assert calls["count"] == 1

    missing = client.get("/research/explore")
    assert missing.status_code == 400
    assert missing.get_json()["error"]


def test_quick_checks_endpoint(client):
    response = client.get("/ops/quick-checks")
    assert response.status_code == 200
    payload = response.get_json()
    assert "summary" in payload
    assert "checks" in payload
    assert payload["summary"]["passed"] >= 0
    assert any(check["id"] == "api_reachability" for check in payload["checks"])


def test_settings_round_trip(client):
    initial = client.get("/settings")
    assert initial.status_code == 200
    payload = initial.get_json()
    assert "toggles" in payload
    assert "auto_refresh" in payload

    update = client.patch(
        "/settings",
        json={"auto_refresh": False, "toggles": {"analyze": True, "simulate": True}},
    )
    assert update.status_code == 200
    patched = update.get_json()
    assert patched["auto_refresh"] is False
    assert patched["toggles"]["analyze"] is True
    assert patched["toggles"]["simulate"] is True

    refreshed = client.get("/settings").get_json()
    assert refreshed["toggles"]["analyze"] is True
    assert refreshed["auto_refresh"] is False
