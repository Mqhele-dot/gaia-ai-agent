# Gaia 2 Mission Control

Gaia is an operator-controlled autonomous research and evaluation system focused on high-impact economic and environmental technology. This branch is the Gaia 2 foundation build: one Flask backend, one compact Mission Control UI, a persistent event ledger, capsules, research, analysis, simulation, evaluation, and halt/resume controls.

## Codespaces quick start

Open this repository in GitHub Codespaces using branch:

`upgrade/gaia-2-foundation`

The dev container installs `requirements.txt` automatically and forwards port `8000`.

Run:

```bash
cp .env.example .env
python main.py
```

Then open the forwarded **Gaia Mission Control** port.

For the first test keep autonomy disabled. Trigger a cycle manually from the **Run Gaia once** button. When ready to test the scheduler:

```bash
export GAIA_AUTONOMY_ENABLED=1
export GAIA_RESEARCH_INTERVAL=60
python main.py
```

> Codespaces must remain running for background autonomy to continue.

## Tests

```bash
pytest -q
```

## Current architecture

```text
Mission Control (vanilla HTML/CSS/JS)
              |
          Flask API
              |
        Gaia Engine
      /      |       \
 Research  Reasoning  Policy
      \      |       /
       Store + Events
```

### Core files

- `gaia/app.py` — API and dashboard server
- `gaia/core/engine.py` — autonomous run orchestrator
- `gaia/core/store.py` — capsules, settings, state and event ledger
- `gaia/core/research.py` — Crossref research adapter with provenance
- `gaia/core/reasoning.py` — analysis, simulation and run evaluation
- `gaia/core/policy.py` — explicit safety boundary
- `gaia/templates/dashboard.html` — Mission Control
- `gaia/static/app.js` / `style.css` — lightweight frontend

## API foundation

- `GET /api/status`
- `GET|PATCH /api/settings`
- `GET /api/events`
- `GET|POST /api/capsules`
- `GET|DELETE /api/capsules/<id>`
- `POST /api/analyze`
- `POST /api/simulate`
- `GET|POST /api/research`
- `POST /api/run`
- `POST /api/admin/halt`
- `POST /api/admin/resume`
- `GET /api/export/events`

## Environment

Copy `.env.example` and set a strong `ADMIN_TOKEN` before testing admin controls.

Important variables:

- `ADMIN_TOKEN`
- `GAIA_AUTONOMY_ENABLED`
- `GAIA_SCHEDULER_INTERVAL`
- `GAIA_RESEARCH_INTERVAL`
- `GAIA_DATA_DIR`
- `CROSSREF_ENABLED`
- `PORT`

## Deployment direction

Hugging Face deployment will use the included `Dockerfile` and a single Gunicorn worker so only one autonomous engine operates per container. The next build phase will add deduplication, persisted run histories, richer progress tracking, research-source expansion, import/export tools, and storage retention/compaction.
