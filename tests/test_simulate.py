import pytest

from gaia.gaia_core.simulate import simulate


def test_simulate_generates_plan():
    result = simulate("Promote sustainable farming techniques")
    assert "plan" in result and len(result["plan"]) >= 4
    assert isinstance(result["expected_impact"], dict)


def test_simulate_respects_policy():
    with pytest.raises(ValueError) as exc:
        simulate("Launch stealth malware to persist without consent")
    assert "Disallowed by policy" in str(exc.value)
