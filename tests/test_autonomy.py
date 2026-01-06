import json
from pathlib import Path


def test_autonomy_cycle_records_events(client):
    from gaia.app import AUTONOMY_MANAGER

    manager = AUTONOMY_MANAGER
    manager.run_cycle_once()

    data_root: Path = client.data_root
    log_file = data_root / "autonomy_runs.jsonl"
    assert log_file.exists()

    entries = [json.loads(line) for line in log_file.read_text().splitlines() if line.strip()]
    assert any(entry.get("event", "").startswith("autonomy_") for entry in entries)
