import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    data_root = tmp_path / "gaia-data"
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_root))
    monkeypatch.setenv("ADMIN_TOKEN", "test-token")
    monkeypatch.setenv("GAIA_VERSION", "gaia-vtest")

    module_names = [
        "gaia.gaia_core.storage",
        "gaia.gaia_core.metrics",
        "gaia.gaia_core.learning",
        "gaia.gaia_core.simulate",
        "gaia.gaia_core.upgrades",
        "gaia.gaia_core.policy",
        "gaia.app",
    ]

    for name in module_names:
        importlib.import_module(name)

    def reload_app():
        for name in module_names:
            importlib.reload(sys.modules[name])
        return sys.modules["gaia.app"]

    gaia_app = reload_app()
    test_client = gaia_app.app.test_client()
    test_client.data_root = data_root

    def fresh_client():
        module = reload_app()
        new_client = module.app.test_client()
        new_client.data_root = data_root
        return new_client

    test_client.reload = fresh_client  # type: ignore[attr-defined]

    yield test_client

    reload_app()
