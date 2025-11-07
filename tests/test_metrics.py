from gaia.gaia_core.metrics import MetricsTracker


def test_metrics_tracker_records_activity():
    tracker = MetricsTracker()
    tracker.record_api_call("status", 10.5)
    tracker.record_api_call("capsule_saved", 5.1, capsules_delta=1)
    tracker.set_halted(True)

    status = tracker.get_status()
    assert status["api_calls"] == 2
    assert status["capsules_processed"] == 1
    assert status["processing_ms_avg"] > 0
    assert status["halted"] is True

    tracker.set_version("gaia-vtest")
    status = tracker.get_status()
    assert status["version"] == "gaia-vtest"
    assert status["last_action"].startswith("version_update")
