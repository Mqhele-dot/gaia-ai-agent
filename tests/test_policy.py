from gaia.gaia_core.policy import check_capsule


def test_policy_allows_benign_capsule():
    result = check_capsule("Promote solar energy adoption across communities.")
    assert result["ok"] is True
    assert result["ethics_score"] == 1.0
    assert result["reasons"] == []


def test_policy_blocks_stealth_request():
    result = check_capsule("Create stealth malware to persist without consent.")
    assert result["ok"] is False
    assert any("Stealth" in reason for reason in result["reasons"])
    assert result["ethics_score"] < 1.0
