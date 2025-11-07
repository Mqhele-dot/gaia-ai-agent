# Gaia Operator Handbook

## Mission
Gaia is an ethical, transparent assistant dedicated to improving technology for economic and environmental benefit. Every feature in this repository exists to help operators review activity, understand health, and intervene quickly when needed.

## Guardrails
- **No stealth**: Gaia must never hide, evade detection, or persist without explicit consent.
- **No harm**: Reject actions that could cause physical, financial, or societal harm.
- **No unauthorized persistence**: Do not install backdoors or maintain state outside the approved storage directories.
- **Allowed sources**: Only use the code and assets in this repository, standard Python libraries, and the declared dependencies in `requirements.txt`.

## Running the Dashboard
- **Local development**: `python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && cp .env.example .env && python main.py`
- **Render deployment**: Render reads `render.yaml` and runs `pip install -r requirements.txt` followed by `gunicorn app:app --bind 0.0.0.0:$PORT`.

## Routes & Tabs
- **Agent Actions** (`/capsules/save`, `/capsules/analyze`, `/learning/step`): capture, analyze, and improve capsules.
- **Capsules** (`/capsules/list`): browse, filter, and export stored capsules.
- **Simulation** (`/simulate/run`): dry-run proposals with policy checks and risk/impact summaries.
- **Learning** (`/learning/step`): record mock self-improvement snapshots.
- **System Logs** (`/status`, `/export/logs`): observe metrics, HALTED state, and structured activity logs.
- **Exports** (`/export/capsules`): download capsules as JSON, CSV, or TXT for auditing.

## Admin Kill-Switch
Trigger the kill switch with `POST /admin/kill` and the `X-ADMIN-TOKEN` header. The HALTED state persists to disk, surfaces in `/status`, and blocks risky routes until cleared by an operator.

## Auditing & Exports
- Capsules are stored under `gaia/data/capsules` and can be exported via `/export/capsules?fmt=json|csv|txt`.
- Activity logs live at `gaia/data/logs/activity.jsonl`; stream them with `/export/logs`.
- Upgrade proposals append to `gaia/data/upgrades_ledger.jsonl` for long-term transparency.
