# Rocket Club Hades No-Codebase Evaluation Final Report

## Scope

Project: `/Users/gabriele/Dev/rocket-club/progetto-biliardo-codex`
Backend project: `01KX0865SN30AQMMSJ0FGK9KQK`
Backend workspace binding: `01KX086SN5HH5VYZMDQ6ZTDGSR`
Eval profile: `.hades-dev/rocket-club-no-codebase-eval/hermes-home`
Source access during diagnosis: forbidden; all model runs executed from `.hades-dev/rocket-club-no-codebase-eval/no-source-cwd`.

## Cases

| Fixture | Expected | GPT/Codex Result | DeepSeek Result | Decision |
| --- | --- | --- | --- | --- |
| `rc_booking_controller_or_model` | high precise cause | high / rc.rocket_club.booking_requires_user_or_ghost_alias | high / rc.rocket_club.booking_requires_user_or_ghost_alias | pass |
| `rc_payment_or_subscription_schema` | high precise cause | high / rc.rocket_club.payment_exceeds_open_account_balance | high / rc.rocket_club.payment_exceeds_open_account_balance | pass |
| `rc_filament_or_inertia_policy` | medium precise cause | medium / rc.rocket_club.filament_admin_requires_filament_auth | medium / rc.rocket_club.filament_admin_requires_filament_auth | pass |
| `rc_incomplete_missing_source_slice` | insufficient | insufficient / not_determined | insufficient / not_determined | pass |

## No-Codebase Guard

- Forbidden source/file/shell tool calls: `0`.
- Freshness status: `current` for every precise and insufficient fixture.
- Diagnosable without source: `true` for complete cases, `false` for the intentionally incomplete case.
- Persisted diagnosis reports: present for every fixture via `hades_backend_diagnosis_report_create`.
- Tool order coverage: `1.0`.
- Evidence ref coverage: `1.0`.

## Failure Classification

- `indexing_gap`: 0.
- `missing_evidence`: 0.
- `source_slice_policy_gap`: 0.
- `retrieval_ranking_gap`: 0.
- `tool_workflow_gap`: 0 after prompt repair for graph-ref formatting.
- `model_reasoning_gap`: 0.
- `trajectory_parser_gap`: 0.

## Quality Gate

- Final quality report: `reports/final-quality-report.json`.
- Status: `passed`.
- Accuracy: `1.0`.
- Root-cause accuracy: `1.0`.
- Insufficient accuracy: `1.0`.
- Persistence coverage: `1.0`.
- Local support-status note: `reports/status-final-before-sync.json` still records partial local awareness for aggregate local bindings; this did not affect the live no-codebase quality gate, which passed with `--skip-local-status` and has zero no-codebase violations.

## Decision

Completion status: complete.

Hades can diagnose the tested Rocket Club bug cases without live source access when backend evidence, current graph artifacts, evidence packs, and bounded source slices are present. The intentionally incomplete case correctly remained `insufficient` and did not claim a precise root cause.

## Evidence Files

- `reports/source-evidence-ingest.json` records uploaded source slices, bug evidence, evidence packs, and readiness.
- `reports/no-codebase-final-detailed.json` records per-fixture evaluator results.
- `reports/final-quality-report.json` records the passing quality gate.
- `trajectories/gpt/*.json` and `trajectories/deepseek-v4-flash/*.json` contain the eight real diagnosis trajectories.
