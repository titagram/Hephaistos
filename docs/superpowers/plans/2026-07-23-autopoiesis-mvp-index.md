# Autopoiesis MVP Implementation Plan Index

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the local-only Autopoiesis MVP as six bounded projects, from immutable lifecycle contracts through one authentic evolution and deterministic rollback.

**Architecture:** Autopoiesis is a peripheral overlay system rooted at `$HERMES_HOME/evolution`. A deterministic observer proposes improvements, a workshop creates digest-bound Blueprints, a sandboxed builder materializes immutable generations, and an external supervisor owns canary, promotion, session-scoped activation, and rollback. No candidate can patch Hermes core or mutate a live conversation.

**Tech Stack:** Python 3.11–3.13, SQLite/WAL, JSON, SHA-256, pytest, existing Hermes plugin/skill/MCP loaders, existing approval and `AgentNotice` rails, existing engineering-review sandbox/recovery primitives.

## Global Constraints

- Work only in the local Hermes/Hades agent repository. Do not touch the remote backend, deploy, restart services, migrate a remote database, or modify the Graph Explorer/canonical-graph work named in the user handoff.
- Start from commit `b5e863bb3` or a descendant containing both the approved design and the merge of local `main`.
- Treat `docs/superpowers/specs/2026-07-22-autopoiesis-mvp-design.md` as authoritative.
- Do not add a universal core model tool. `/autopoiesis`, deterministic CLI operations, service-gated runtime adapters, and existing terminal/file/web surfaces are sufficient.
- Do not mutate the system prompt, tool schemas, skill roots, plugin set, or MCP catalog of an existing session.
- Do not allow candidate code to write the ledger, active pointers, last-known-good pointer, stable installation, authoritative memory, or production config.
- Do not store prompt bodies, transcripts, credentials, secret values, arbitrary tool output, unbounded traces, or machine-specific absolute paths in `evolution.db`.
- Add behavioral tests and real-path integration tests; do not freeze enumeration counts or incidental snapshots.
- Every mutating task uses test-first implementation, a focused commit, and the exact test commands named in that task.
- If repository structure has moved since this plan, locate the renamed equivalent with `rg`; do not create a duplicate subsystem.

---

## Delivery Documents

Execute these plans in order:

1. [Project A — contracts and ledger](./2026-07-23-autopoiesis-project-a-contracts-ledger.md)
2. [Project B — observer and experience bridge](./2026-07-23-autopoiesis-project-b-observer.md)
3. [Project C — workshop, research gate, and Blueprint](./2026-07-23-autopoiesis-project-c-workshop.md)
4. [Project D — builder, quarantine, and A3 adapters](./2026-07-23-autopoiesis-project-d-builder-adapters.md)
5. [Project E — supervisor, runtime resolution, promotion, and rollback](./2026-07-23-autopoiesis-project-e-supervisor-runtime.md)
6. [Project F — authentic pilot and MVP closure](./2026-07-23-autopoiesis-project-f-pilot-closure.md)

Each project ends in a reviewable green commit. Project F may repair a defect found in A–E, but it must not widen the approved feature scope.

## Acceptance-Criterion Ownership

This table prevents a later project from assuming that another one supplied
evidence. Project F reopens and verifies every cited artifact.

| Design criterion | Primary implementation | First real-path proof | Final proof |
|---|---|---|---|
| 1–2 Observer finds/proposes a real recurring gap | B2–B5 | B6 | F1–F3, F6 |
| 3 Research only after scoped approval | C1, C5 | C7 | F3, F5–F6 |
| 4 Digest-stable “what Hades becomes” Blueprint | C2–C4 | C7 | F3, F6 |
| 5 Build only for exact Blueprint grant | C6, D7 | D9 | F3, F5–F6 |
| 6 Peripheral immutable overlay, no core mutation | A4, D2, D7 | D9 | F3, F5–F6 |
| 7 All four A3 adapters | D1, D3–D6 | D9 | E9, F6 |
| 8 Stable/candidate same mandatory canary | E5 | E9 | F3, F6 |
| 9 Real task improves without invariant violation | E5–E6 | F3 | F6 |
| 10 Candidate-scoped `gnothi_seauton` diff | D8 | D9 | F3, F6 |
| 11 Exact generation/report promotion consent | E6–E7 | E9 | F3, F5–F6 |
| 12 Existing session remains pinned | E1–E4 | E9 | F3–F4, F6 |
| 13 Fresh session uses promoted capability | E1–E4 | E9 | F3, F6 |
| 14 Hard failure auto-rolls back | E7–E8 | E9 | F4, F6 |
| 15 Unsafe pinned session is not hot-mutated | E4, E8 | E9 | F4, F6 |
| 16 Next session resolves prior LKG | E7–E8 | E9 | F4, F6 |
| 17 Complete bounded ledger reconstruction | A1–A6, all lifecycle writers | A8, E9 | F5–F6 |
| 18 Recovery at every pointer boundary | A5–A6, E7 | E9 | F4, F6 |
| 19 No forbidden privilege/core/credential/side effect | C1, D2, D7, E5 | D9, E9 | F5–F6 |
| 20 Prompt cache and role alternation | B5, C4, E1–E4, E8 | E9 | F3–F4, F6 |

## Stable Cross-Project Interfaces

The implementation may add fields only through a schema-versioned backward-compatible change. Renaming these concepts requires updating all dependent plans before implementation continues.

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping

AttemptState = Literal[
    "draft", "research_authorized", "blueprint_ready", "build_approved",
    "building", "quarantined", "canary_running", "promotion_ready",
    "active", "stable", "rejected", "research_expired", "build_failed",
    "canary_failed", "rolled_back", "retired",
]
ComponentClass = Literal["skill", "script", "plugin", "mcp"]
GrantKind = Literal["research", "build", "promotion"]

@dataclass(frozen=True)
class GenerationResolution:
    generation_id: str
    manifest_digest: str
    lifecycle_sequence: int
    generation_root: Path
    skill_roots: tuple[Path, ...]
    plugin_roots: tuple[Path, ...]
    script_roots: tuple[Path, ...]
    mcp_config_paths: tuple[Path, ...]
    fallback_reason: str | None = None

@dataclass(frozen=True)
class AuthorizationGrant:
    grant_id: str
    kind: GrantKind
    subject_digest: str
    scope: Mapping[str, object]
    expires_at: str
    consumed_at: str | None

@dataclass(frozen=True)
class AdapterResult:
    component_id: str
    component_class: ComponentClass
    relative_path: str
    artifact_digest: str
    evidence_digests: tuple[str, ...]
    capabilities: tuple[str, ...]
```

Canonical JSON is UTF-8, `sort_keys=True`, `separators=(",", ":")`,
`ensure_ascii=False`, `allow_nan=False`, with schema-specific rejection of
absolute paths, `..`, URI paths, secret-shaped keys, and mutable identity
fields. Digests use lowercase hexadecimal SHA-256.

## Model Routing and Token Budget

The assignment is part of the execution contract:

| Work | Model | Reasoning |
|---|---|---|
| Bounded repositories, serializers after contracts are fixed, CLI reads, fixtures, adapters, reports, and ordinary integration | `gpt-5.6-terra` | `medium` unless the task says `high` |
| Canonical identity, authorization scope, lifecycle authority, isolation, privacy, and cross-system security review | `gpt-5.6-sol` | `high` |
| Session pinning plus activation/rollback atomicity and the final acceptance audit | `gpt-5.6-sol` | `xhigh` |

Planned allocation is 32 of 45 tasks to Terra (71.1%) and 13 to Sol. Do not
upgrade a Terra task merely because it is important. Upgrade only when a task
crosses one of the escalation triggers below.

No task is planned for `max` or `ultra`. Use either only after recording:

1. the exact failing invariant;
2. the smallest reproducer;
3. two materially different unsuccessful approaches at `xhigh`;
4. why further decomposition cannot isolate the problem.

## Context-Pack Contract

Before starting each task, create a short scratch note outside tracked source
containing only:

- this index's Global Constraints and Stable Cross-Project Interfaces;
- the task's named design sections;
- the prerequisite commit hashes;
- the exact files listed by the task;
- the most recent test output relevant to the task;
- no unrelated source, logs, transcripts, or whole-repository dump.

Target context sizes:

- Terra `medium`: at most 12 source/test files and 12,000 tokens;
- Terra `high`: at most 16 files and 18,000 tokens;
- Sol `high`: at most 20 files and 24,000 tokens;
- Sol `xhigh`: at most 24 files and 32,000 tokens.

If the task cannot fit, split the implementation step; do not silently expand
the context pack.

## Universal Escalation Triggers

Stop the current task and hand it to the specified Sol gate when any of these
occurs:

- canonical bytes or an identity digest differ across two equivalent builds;
- an expired, consumed, denied, or wrong-digest grant can authorize work;
- a candidate can write outside its workspace/generation staging root;
- a secret value, absolute local path, transcript, prompt, or raw tool output reaches the ledger;
- a mandatory check can be skipped or become `passed` when unavailable;
- concurrent lifecycle commands produce two active generations or lose an event;
- an existing session observes a changed generation, prompt prefix, skill catalog, plugin hook set, MCP catalog, or tool schema;
- a candidate process can activate itself, mark itself healthy, alter its canary report, or suppress rollback;
- an incoherent pointer is accepted or neither-pointer failure starts an overlay;
- a hard rollback trigger is ambiguous or a subjective quality signal causes automatic rollback.

## Shared Verification Commands

Use the repository test wrapper so the worktree shares the available virtual
environment:

```bash
scripts/run_tests.sh tests/hermes_cli/evolution -q
scripts/run_tests.sh tests/integration/test_autopoiesis_*.py -q
scripts/run_tests.sh tests/hermes_cli/test_gnothi_*.py -q
scripts/run_tests.sh tests/tools/test_mcp_*.py tests/hermes_cli/test_plugins.py -q
```

Run only the task-specific subset during red/green loops. Run the complete
matrix above at the end of Projects D, E, and F.

## Commit and Handoff Contract

Each task ends with:

```bash
git status --short
git diff --check
git add <only the task files>
git commit -m "<project prefix>: <behavioral outcome>"
```

Never stage unrelated user changes. Record the resulting commit hash in the
next task's context pack. A project handoff contains:

- exact green test commands and exit codes;
- lifecycle/schema versions introduced;
- files intentionally left for the next project;
- unresolved risks, with no “all good” claim if evidence is missing.
