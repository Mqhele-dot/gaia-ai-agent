"""HF Spaces entrypoint for the Gaia dashboard."""
from __future__ import annotations

import os

from gaia.app import app as flask_app

app = flask_app


if __name__ == "__main__":
    port = int(os.getenv("PORT", "7860"))
    app.run(host="0.0.0.0", port=port)
