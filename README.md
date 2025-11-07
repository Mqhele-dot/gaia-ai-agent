---
title: Gaia Dashboard
emoji: "🌍"
colorFrom: indigo
colorTo: green
sdk: docker
pinned: false
---

# Gaia Dashboard

Gaia is an ethical, transparent, self-improving assistant focused on delivering
technology that benefits both the economy and the environment. This repository
packages a deployable dashboard and JSON-backed API that highlights every
interaction, exposes runtime metrics, and provides an emergency kill switch.

## Features

- **Capsule management** – Save, list, analyze, simulate, and export ideas.
- **Transparent logging** – Every action is written to `data/logs/activity.jsonl`.
- **Mock self-improvement** – Trigger deterministic learning steps that persist
  versioned snapshots.
- **Policy-first safeguards** – All inputs are checked against explicit rules to
  prevent stealth, harm, or unauthorized persistence.
- **Responsive dashboard** – Tabbed layout with dark mode, live metrics, and
  searchable capsule history.

## Project Layout

```
gaia/
  app.py                # Flask application
  main.py               # Dev server entrypoint (run locally)
  requirements.txt
  render.yaml
  .env.example
  README.md
  gaia_core/
    policy.py
    learning.py
    simulate.py
    metrics.py
    storage.py
    upgrades.py
  templates/
    dashboard.html
  static/
    style.css
    app.js
  data/
    capsules/
    logs/
    models/
```

## Getting Started

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python main.py
```

The dashboard will be available at [http://localhost:8000](http://localhost:8000).

## Environment Configuration

| Variable       | Default        | Description                                                       |
|----------------|----------------|-------------------------------------------------------------------|
| `ADMIN_TOKEN`  | `changeme`     | Required header for `/admin/kill` requests.                       |
| `GAIA_VERSION` | `gaia-v1.0`    | Initial version string displayed in `/status`.                    |
| `GAIA_DATA_DIR`| `gaia/data`    | Root directory for capsules, logs, state, and upgrade ledgers.    |

## Security and Policy

Gaia refuses to perform any action that violates its explicit policy rules:

1. No stealth or hidden behavior.
2. No unauthorized persistence or backdoors.
3. No harm to people, infrastructure, or the environment.
4. No illegal or exploitative activity.

If a request violates these rules the API responds with HTTP 400 and the message
`"Disallowed by policy."` along with the reasons. The policy implementation is
fully auditable in [`gaia_core/policy.py`](gaia/gaia_core/policy.py).

### Admin Kill Switch & HALTED State

Send a `POST /admin/kill` request with header `X-ADMIN-TOKEN` that matches the
configured token. The application records the action, sets the HALTED flag, and
persists it to `gaia/data/state.json` so that restarts remain paused until an
operator explicitly clears the state. While HALTED:

- `/simulate/run` and `/upgrades/propose` return HTTP 423 and log a
  `*_blocked` event.
- The dashboard shows a red HALTED banner and `/status` reports `"halted": true`.

To resume service, review the situation and then update `gaia/data/state.json`
to `{"halted": false}` (or remove the file) before restarting the process.

### No-Stealth Guarantee

Gaia explicitly refuses to:

- Hide processes, network activity, or filesystem artifacts.
- Persist beyond the consent of the operator or user.
- Disable or interfere with security tooling.
- Misrepresent its actions or bypass audit mechanisms.

All activity is logged, and the `/status` endpoint exposes API call counts,
processing averages, and the last action taken.

## Deployment

### Render

The included [`render.yaml`](render.yaml) configures a Render web service. Build
runs `pip install -r requirements.txt` and the server starts via Gunicorn:

```
gunicorn gaia.app:app --bind 0.0.0.0:$PORT
```

### Hugging Face Spaces

Gaia ships with a ready-to-use Docker configuration that Hugging Face Spaces
can execute directly.

1. Create a new **Docker** Space.
2. Upload the repository contents (including `Dockerfile`, `app.py`, and
   `requirements.txt`).
3. Set the desired `ADMIN_TOKEN`, `GAIA_VERSION`, and `GAIA_DATA_DIR` secrets in
   the Space settings (the defaults from `.env.example` also work).

The Docker build installs dependencies and starts Gunicorn via the command

```
gunicorn gaia.app:app --bind 0.0.0.0:7860
```

The provided root-level [`app.py`](app.py) mirrors this behaviour for local
testing or for Spaces configured with the "Python" runtime that expects an
`app` object in the module namespace.

## API Reference

| Method | Route               | Description                                      |
|--------|---------------------|--------------------------------------------------|
| GET    | `/`                 | Serve the dashboard UI.                          |
| GET    | `/status`           | Runtime metrics and health.                      |
| POST   | `/capsules/save`    | Persist a capsule `{text, tag}`.                 |
| GET    | `/capsules/list`    | List capsules, optional `tag` / `q` filtering.   |
| POST   | `/capsules/analyze` | Length, token, and ethics analysis for text.     |
| POST   | `/simulate/run`     | Deterministic simulation with impact scores.     |
| POST   | `/learning/step`    | Mock learning update, records snapshot.          |
| POST   | `/upgrades/propose` | Validate upgrade proposal against policy.        |
| POST   | `/admin/kill`       | Toggle halt flag (requires `X-ADMIN-TOKEN`).     |
| GET    | `/export/logs`      | Download `activity.jsonl`.                       |
| GET    | `/export/capsules`  | Export capsules in `json`, `csv`, or `txt`.      |

### cURL Examples

```bash
# Health and metrics
curl -s http://localhost:8000/status | jq

# Save, list, analyze, and simulate a capsule
curl -s -X POST http://localhost:8000/capsules/save \
  -H "Content-Type: application/json" \
  -d '{"text": "Launch a community solar project", "tag": "solar"}' | jq

curl -s "http://localhost:8000/capsules/list?tag=solar" | jq

curl -s -X POST http://localhost:8000/capsules/analyze \
  -H "Content-Type: application/json" \
  -d '{"text": "Launch a community solar project"}' | jq

curl -s -X POST http://localhost:8000/simulate/run \
  -H "Content-Type: application/json" \
  -d '{"text": "Launch a community solar project"}' | jq

# Learning snapshot
curl -s -X POST http://localhost:8000/learning/step \
  -H "Content-Type: application/json" \
  -d '{"engagement": 7.5, "success_rate": 0.9, "feedback_score": 0.8}' | jq

# Upgrade proposal
curl -s -X POST http://localhost:8000/upgrades/propose \
  -H "Content-Type: application/json" \
  -d '{"proposal": "Deploy transparent solar panels", "rationale": "Boost clean energy"}' | jq

# Export logs and capsules
curl -s -OJ http://localhost:8000/export/logs
curl -s -OJ "http://localhost:8000/export/capsules?fmt=csv"

# Trigger the kill switch (requires token)
curl -s -X POST http://localhost:8000/admin/kill \
  -H "X-ADMIN-TOKEN: $(grep ADMIN_TOKEN .env | cut -d'=' -f2)" | jq
```

## Logging

Each action writes a JSON line to `gaia/data/logs/activity.jsonl` using the
following patterns:

```
{"ts":"ISO8601","event":"capsule_saved","capsule_id":"...","tag":"...","len":123}
{"ts":"ISO8601","event":"simulate","capsule_id":"...","safety":"pass","risk":"low"}
{"ts":"ISO8601","event":"learning_step","delta_score":0.07,"version":"gaia-v1.2"}
```

Upgrade proposals append to `gaia/data/upgrades_ledger.jsonl` with the recorded
decision, ethics score, and notes for long-term auditing.

## Screenshots

Add deployment screenshots here once available.

## Testing

```bash
pytest
```

Integration tests cover capsule save/list/analyze/simulate/export and kill
switch state propagation. Unit tests validate the policy guard rails.
