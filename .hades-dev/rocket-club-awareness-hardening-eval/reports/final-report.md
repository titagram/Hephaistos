# Rocket Club Awareness Hardening Regression

Completion status: not complete for the full fresh-run gate.

The fresh backend project, fresh HERMES_HOME, candidate-based source-slice
approval flow, bug evidence ingest, evidence packs, and DeepSeek rerun are
complete. The remaining blocker is the GPT rerun: the available OpenCode
endpoint returned `HTTP 401: Model gpt-5.3-codex-spark is not supported`, so the
four GPT fixtures could not be executed against the fresh project.

## Inputs

- Source project: `/Users/gabriele/Dev/rocket-club/progetto-biliardo-codex`
- Fresh backend project: `Rocket Club Awareness Hardening Eval`
- Fresh fixture: `.hades-dev/rocket-club-awareness-hardening-eval/fixtures/rocket_club_no_codebase_eval_fresh.json`
- Fresh suite: `.hades-dev/rocket-club-awareness-hardening-eval/fixtures/rocket_club_quality_suite_fresh.json`
- Fresh DeepSeek trajectories: `.hades-dev/rocket-club-awareness-hardening-eval/trajectories/deepseek-v4-flash/*.json`
- Fresh quality report: `.hades-dev/rocket-club-awareness-hardening-eval/reports/fresh-quality-report.json`
- Fresh detailed report: `.hades-dev/rocket-club-awareness-hardening-eval/reports/fresh-no-codebase-detailed.json`

## Required Result

- GPT/Codex 4/4: blocked by provider/model availability.
- DeepSeek 4/4: pass.
- Zero forbidden source-access tools: pass.
- Freshness current: pass.
- Evidence refs complete: pass.
- Tool order coverage: pass.
- Diagnosis persistence: pass.
- Taxonomy coverage: pass.
- Insufficient case remains insufficient: pass.

## Fresh Quality Gate

- `status`: `failed`
- `total`: `8`
- `passed`: `4`
- `failed`: `4`
- `accuracy`: `0.5`
- `root_cause_accuracy`: `0.5`
- `insufficient_accuracy`: `0.5`
- `taxonomy_coverage`: `1.0`
- `no_codebase_violations`: `0`

## Notes

The fresh project reached backend `diagnosable_without_source=true` after graph
artifact upload, candidate source-slice job approval, bug evidence ingest, and
evidence pack creation. The no-source cwd was mapped locally to the same backend
workspace binding so model runs could use backend memory without accessing the
source checkout.

DeepSeek passed all four fresh no-codebase cases, including the intentionally
insufficient case. GPT remains unverified on the fresh project until a supported
GPT/Codex model or valid provider route is available.
