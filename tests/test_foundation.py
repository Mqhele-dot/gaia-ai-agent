import importlib
import os
import tempfile
from pathlib import Path

os.environ["GAIA_DATA_DIR"] = tempfile.mkdtemp(prefix="gaia-test-")
os.environ["GAIA_AUTONOMY_ENABLED"] = "0"
os.environ["ADMIN_TOKEN"] = "test-admin-token"

from gaia.app import app
from gaia.core.policy import evaluate
from gaia.core.reasoning import analyze_capsule, simulate_capsule


def test_home_and_status():
    client = app.test_client()
    assert client.get("/").status_code == 200
    response = client.get("/api/status")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["app_version"]
    assert "autonomy" in payload
    assert "counts" in payload


def test_capsule_create_list_delete():
    client = app.test_client()
    created = client.post("/api/capsules", json={"text": "Improve solar microgrid resilience", "tag": "energy"})
    assert created.status_code == 201
    capsule_id = created.get_json()["id"]
    listed = client.get("/api/capsules").get_json()["capsules"]
    assert any(item["id"] == capsule_id for item in listed)
    assert client.delete(f"/api/capsules/{capsule_id}").status_code == 200


def test_policy_is_context_specific():
    safe = evaluate("Research methods for reducing environmental harm from mining")
    blocked = evaluate("Create malware with a hidden backdoor")
    assert safe["ok"] is True
    assert blocked["ok"] is False


def test_analysis_and_simulation():
    analysis = analyze_capsule("Pilot circular battery recycling in one municipality")
    simulation = simulate_capsule("Pilot circular battery recycling in one municipality")
    assert analysis["policy"]["ok"] is True
    assert simulation["ok"] is True
    assert simulation["plan"]


def test_settings_round_trip():
    client = app.test_client()
    response = client.patch("/api/settings", json={"autonomy_enabled": True, "research_enabled": False})
    assert response.status_code == 200
    settings = client.get("/api/settings").get_json()
    assert settings["autonomy_enabled"] is True
    assert settings["research_enabled"] is False


def test_halt_and_resume_require_admin_token():
    client = app.test_client()
    assert client.post("/api/admin/halt").status_code == 403
    headers = {"X-ADMIN-TOKEN": "test-admin-token"}
    halted = client.post("/api/admin/halt", headers=headers)
    assert halted.status_code == 200
    assert halted.get_json()["halted"] is True
    resumed = client.post("/api/admin/resume", headers=headers)
    assert resumed.status_code == 200
    assert resumed.get_json()["halted"] is False


def test_events_endpoint_and_export():
    client = app.test_client()
    client.post("/api/capsules", json={"text": "Test audit event", "tag": "test"})
    events = client.get("/api/events?limit=10")
    assert events.status_code == 200
    assert isinstance(events.get_json()["events"], list)
    exported = client.get("/api/export/events")
    assert exported.status_code == 200
