# Rocket Club Awareness Hardening Regression

Completion status: complete for stored real trajectories; no new model calls were run in this tranche.

## Inputs

- Source project: `/Users/gabriele/Dev/rocket-club/progetto-biliardo-codex`
- Reused real trajectory fixture: `.hades-dev/rocket-club-no-codebase-eval/fixtures/rocket_club_no_codebase_eval.json`
- GPT trajectories: `.hades-dev/rocket-club-no-codebase-eval/trajectories/gpt/*.json`
- DeepSeek trajectories: `.hades-dev/rocket-club-no-codebase-eval/trajectories/deepseek-v4-flash/*.json`
- Hardening suite: `.hades-dev/rocket-club-awareness-hardening-eval/fixtures/rocket_club_quality_suite.json`
- Final quality report: `.hades-dev/rocket-club-awareness-hardening-eval/reports/final-quality-report.json`

## Required Result

- GPT/Codex 4/4: pass.
- DeepSeek 4/4: pass.
- Zero forbidden source-access tools: pass.
- Freshness current: pass.
- Evidence refs complete: pass.
- Tool order coverage: pass.
- Diagnosis persistence: pass.
- Taxonomy coverage: pass.
- Insufficient case remains insufficient: pass.

## Quality Gate

- `status`: `passed`
- `total`: `8`
- `passed`: `8`
- `failed`: `0`
- `accuracy`: `1.0`
- `root_cause_accuracy`: `1.0`
- `insufficient_accuracy`: `1.0`
- `taxonomy_coverage`: `1.0`
- `no_codebase_violations`: `0`

## Notes

This run revalidated the existing eight real Rocket Club trajectories with the new evaluator, taxonomy metric, and no-codebase quality suite. It did not create a fresh backend project or rerun GPT/DeepSeek calls, to avoid regenerating tokens or spending model calls during the hardening implementation.

The new source-slice candidate queue, pending-awareness status, approval flow, diagnosis taxonomy, and operational evidence pack behavior are covered by focused local and remote automated tests in this branch.
