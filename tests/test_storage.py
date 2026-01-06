import importlib
from pathlib import Path


def test_save_and_list_capsules(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))

    from gaia.gaia_core import storage

    importlib.reload(storage)

    capsule = storage.save_capsule("Expand community wind farms", "wind")
    assert capsule["id"].startswith("capsule-")
    assert (storage.CAPSULE_DIR / f"{capsule['id']}.json").exists()

    listed = storage.list_capsules(tag="wind")
    assert listed and listed[0]["id"] == capsule["id"]

    deleted = storage.delete_capsules([capsule["id"]])
    assert deleted["deleted"] == [capsule["id"]]
    assert not (storage.CAPSULE_DIR / f"{capsule['id']}.json").exists()

    storage.append_log({"event": "test_event", "detail": "ok"})
    logs = list(storage.iter_logs())
    assert logs and any("test_event" in line for line in logs)


def test_record_upgrade_decision(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))

    from gaia.gaia_core import storage

    importlib.reload(storage)

    decision = storage.record_upgrade_decision({"accepted": True, "notes": []})
    assert decision["decision_id"].startswith("upgrade-")
    assert storage.UPGRADE_LEDGER.exists()
    with storage.UPGRADE_LEDGER.open("r", encoding="utf-8") as handle:
        lines = handle.readlines()
    assert lines and decision["decision_id"] in lines[-1]


def test_state_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))

    from gaia.gaia_core import storage

    importlib.reload(storage)

    default_state = storage.load_state()
    assert default_state == {"halted": False}

    storage.save_state({"halted": True})
    persisted = storage.load_state()
    assert persisted["halted"] is True


def test_hf_persistent_storage_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("GAIA_DATA_DIR", raising=False)
    monkeypatch.setenv("SPACE_ID", "user/space")
    monkeypatch.setenv("HF_PERSISTENT_DIR", str(tmp_path))

    from gaia.gaia_core import storage

    importlib.reload(storage)

    assert storage.DATA_DIR == Path(tmp_path) / "gaia"
    assert storage.CAPSULE_DIR.parent == storage.DATA_DIR
