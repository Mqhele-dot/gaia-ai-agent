# Gaia Desktop Build Operator Note

## What this build can do
- Run **single bounded coding tasks** through distrustful runtime safeguards.
- Run **release checks** with deterministic reports/hashes.
- Run **long campaigns** (4–5h budget) as many short bounded tasks with checkpoints, replanning, and explicit halt rules.

## What this build should NOT be used for
- Unbounded autonomous loops.
- Silent mutation outside witnessed runtime/tool boundaries.
- Auto-approval of future risky steps.

## Local prerequisites
- Python + dependencies from `requirements.txt`.
- Git repo with writable working tree.
- Local tool availability: `git`, `python`, `pytest`.
- A configured local model/provider for real mutation tasks (current CLI defaults still require integrator intent provider wiring).

## Profile/config path
- Default desktop profile: `gaia/config/desktop_local_profile.json`.
- Includes:
  - repo root
  - artifact/witness paths
  - min disk headroom
  - campaign defaults
  - strict/approval defaults
  - retention defaults

## Start commands

### 1) Single-task mode
```bash
python -m gaia.gaia_core.run_task --objective "your bounded task objective" --repo /path/to/repo
```

### 2) Release-check mode
```bash
python -m gaia.gaia_core.run_release_gate --profile local_16gb --iterations 10 --output-dir gaia/data/eval_reports/release_candidate_local_16gb
```

or via eval alias:
```bash
python -m gaia.gaia_core.run_eval --suite release_candidate --iterations 10 --profile local_16gb --output-dir gaia/data/eval_reports/release_candidate_local_16gb
```

### 3) Long-run campaign mode
```bash
python -m gaia.gaia_core.run_campaign --objective "incremental coding campaign objective" --repo /path/to/repo --hours 5 --profile-path gaia/config/desktop_local_profile.json
```

Desktop wrapper:
```bash
python scripts/run_desktop_campaign.py --objective "incremental coding campaign objective" --repo /path/to/repo --hours 5 --profile-path gaia/config/desktop_local_profile.json
```

## Approval pause/resume behavior
- Campaign pauses on approval boundaries (`AWAITING_APPROVAL`).
- Resume requires explicit approval token; no blanket auto-approval inheritance.

## Inspecting outputs
- Campaign artifacts/checkpoints:
  - `gaia/data/campaigns/<campaign_id>/checkpoint-*.json`
  - `gaia/data/campaigns/<campaign_id>/campaign_result_bundle.json`
  - `gaia/data/campaigns/<campaign_id>/campaign_summary.json`
- Witness artifacts:
  - `gaia/data/witness/`
- Release artifacts:
  - `gaia/data/eval_reports/release_candidate_local_16gb/`

## Safe stop guidance
- Stop by allowing campaign halt conditions to trigger or end by time budget.
- Do not kill in the middle of unsafe mutation without reviewing current lock/checkpoint state.
