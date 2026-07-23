# Autopoiesis Project B — Observer and Experience Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert bounded, recurring local experience evidence into deterministic, deduplicated, ranked evolution suggestions and surface them through the existing out-of-band notice rail without granting research or mutation authority.

**Architecture:** Existing `gnothi_seauton` experience events remain the bounded runtime signal. A fail-soft background observer tails them through a durable cursor, normalizes and imports only safe fields, aggregates deterministic evidence, and upserts suggestion projections backed by append-only ledger events. `AgentNotice` surfaces eligible suggestions after a turn; no chat message is injected.

**Tech Stack:** Existing JSONL experience log, Project A SQLite ledger, SHA-256 identities, deterministic scoring, one bounded background executor, `AgentNotice`, pytest.

## Global Constraints

- Depends on all Project A commits and contracts.
- Read design sections “Evolution Observer”, “Observation path”, “Privacy and Security”, and “Real-path end-to-end pilot”.
- Observer output is a proposal only. It cannot issue a grant, open the web, build, create a workspace, invoke a candidate, or change pointers.
- A fixed event set and scoring-policy version must always produce the same suggestions and order.
- Event and suggestion records contain no user text, prompt text, tool arguments, tool result, raw stack, secret, or absolute path.
- Observer failures must not fail or delay the user's completed turn.

---

## File and Module Map

| Path | Responsibility |
|---|---|
| `hermes_cli/gnothi/events.py` | Versioned bounded `ExperienceEvent` emission |
| `hermes_cli/evolution/experience_bridge.py` | Safe tail/import and durable cursor |
| `hermes_cli/evolution/observer.py` | Aggregation, ranking, eligibility |
| `hermes_cli/evolution/suggestions.py` | Deduplication, suppression, state/read repository |
| `hermes_cli/evolution/notices.py` | Rate-limited `AgentNotice` projection |
| `agent/turn_finalizer.py` | Fail-soft scheduling seam after a turn |
| `hermes_cli/evolution/command.py` | Suggestion list/show and observer status |
| `tests/hermes_cli/evolution/` | Observer unit/integration/privacy tests |

## Deterministic Scoring Policy v1

Every eligible aggregate receives six normalized `[0, 1]` terms:

```text
score =
    0.28 * task_impact
  + 0.24 * recurrence
  + 0.18 * confidence
  + 0.14 * reuse
  + 0.10 * (1 - risk)
  + 0.06 * (1 - expected_cost)
```

- `task_impact`: maximum of the severity weight (`info=.10`, `warning=.35`,
  `error=.70`, `critical=1.00`) and the declared bounded task-impact weight
  (`unknown=.10`, `low=.25`, `medium=.50`, `high=.80`, `critical=1.00`),
  raised to at least `.80` when the event class is a hard capability absence.
- `recurrence`: `min(1, count / 10)`.
- `confidence`: `min(1, distinct_utc_days / 3)`.
- `reuse`: `min(1, distinct_operations / 4 + distinct_components / 8)`.
- `risk`: closed rule table by proposed component class
  (`skill=.15`, `script=.35`, `plugin=.60`, `mcp=.70`, `unknown=.85`).
- `expected_cost`: closed rule table (`skill=.10`, `script=.30`,
  `plugin=.55`, `mcp=.65`, `unknown=.80`).

Round only the final score to six decimal places. Eligibility requires at least
the configured recurrence threshold, two distinct UTC days, nonempty evidence,
and no active/pending generation or open suggestion with the same dedupe key.
Sort by score descending, recurrence descending, task impact descending, risk
ascending, then stable suggestion ID.

## Task 1: Versioned bounded experience events and safe tail import

**Model:** `gpt-5.6-terra`, reasoning `medium`

**Objective:** Make runtime evidence parseable, resumable, and safe to import
without changing existing failure emission behavior.

**Non-goals:** No scoring or suggestion creation.

**Files:**
- Modify: `hermes_cli/gnothi/events.py`
- Create: `hermes_cli/evolution/experience_bridge.py`
- Modify: `hermes_cli/evolution/ledger.py`
- Modify: `tests/hermes_cli/test_gnothi_events.py`
- Create: `tests/hermes_cli/evolution/test_experience_bridge.py`

**Context pack:** Project A ledger; current `events.py` and
`collectors/experience.py`; design privacy rules.

**Interfaces:**

```python
@dataclass(frozen=True)
class ExperienceEvent:
    schema_version: int
    event_id: str
    event_type: str
    generation_id: str
    component_id: str
    capability_id: str
    operation: str
    bounded_signature: str
    failure_class: str
    severity: str
    retry_count: int
    task_impact: str
    recovered: bool
    evidence_refs: tuple[str, ...]
    occurred_at: str

def emit_experience_event(
    *,
    event_type: str,
    generation_id: str,
    component_id: str,
    capability_id: str | None,
    operation: str,
    failure_class: str | None,
    severity: str,
    retry_count: int = 0,
    task_impact: str = "unknown",
    recovered: bool = False,
    evidence_refs: list[str] | None = None,
    occurred_at: str | None = None,
) -> str | None: ...
def import_new_events(ledger: EvolutionLedger, *, max_events: int = 1000,
                      max_bytes: int = 8_388_608) -> ImportResult: ...
```

Ledger schema migration 2 adds `experience_events` with only the fields above
and `observer_cursors(source_id PRIMARY KEY, inode, byte_offset,
last_event_id, updated_at)`.

- [ ] **Step 1: Write failing backward-compatibility tests**

Existing callers may ignore the return value. Valid emission returns an event
ID; an I/O failure returns `None` and never raises. Existing JSONL remains
readable by `ExperienceCollector`.

- [ ] **Step 2: Write failing tail/import tests**

Cover partial trailing line, file rotation/inode change, truncation, duplicate
event ID, oversized line, invalid UTF-8, future schema, bounded scan, and crash
between insert and cursor update.

- [ ] **Step 3: Run red tests**

Run: `scripts/run_tests.sh tests/hermes_cli/test_gnothi_events.py tests/hermes_cli/evolution/test_experience_bridge.py -q`

- [ ] **Step 4: Implement schema-v1 event construction**

Event ID is a digest of safe semantic fields plus timestamp and a local random
nonce; the bounded signature remains deterministic for aggregation. Keep each
serialized line under 4 KiB.

- [ ] **Step 5: Implement schema migration and atomic import**

Insert safe events and advance the cursor in one SQLite transaction. Unknown
or malformed lines advance only through a bounded diagnostic event so one bad
line cannot create an infinite retry loop.

- [ ] **Step 6: Run tests and commit**

Commit: `evolution-b: bridge bounded experience events`

**Escalate if:** importing requires raw tool content, a partial line is lost,
or cursor advancement can skip an uncommitted valid event.

## Task 2: Deterministic aggregation and scoring

**Model:** `gpt-5.6-terra`, reasoning `medium`

**Objective:** Convert imported failures and recoveries into reproducible
aggregate candidates and scores.

**Non-goals:** No suggestion state, notification, or LLM explanation.

**Files:**
- Create: `hermes_cli/evolution/observer.py`
- Create: `tests/hermes_cli/evolution/test_observer_scoring.py`

**Context pack:** Task 1 event record; scoring policy in this plan; no UI files.

**Interfaces:**

```python
@dataclass(frozen=True)
class ObservationAggregate:
    dedupe_key: str
    generation_ids: tuple[str, ...]
    capability_id: str
    component_id: str
    operation: str
    bounded_signature: str
    failure_count: int
    recovery_count: int
    distinct_utc_days: int
    score_terms: Mapping[str, float]
    score: float
    proposed_component_class: ComponentClass | Literal["unknown"]

def aggregate_events(events: Iterable[ExperienceEvent],
                     policy: ObserverPolicy) -> tuple[ObservationAggregate, ...]: ...
```

- [ ] **Step 1: Write failing formula tests**

Use hand-calculated fixtures for every severity and component class. Assert
rounding only after summation and stable ordering under reversed input.

- [ ] **Step 2: Write failing recovery/workaround tests**

Recovered events reduce unresolved evidence but never erase history. A
workaround reference lowers impact through an explicit rule and is retained as
an evidence reference, not arbitrary text.

- [ ] **Step 3: Run red tests**

Run: `scripts/run_tests.sh tests/hermes_cli/evolution/test_observer_scoring.py -q`

- [ ] **Step 4: Implement pure aggregation**

Use no clock, filesystem, config read, random value, or database call inside
`aggregate_events`. Pass policy and events explicitly.

- [ ] **Step 5: Add property tests for determinism**

Permute input order, duplicate already-imported IDs, and split/concatenate
batches. Results must match exactly.

- [ ] **Step 6: Run tests and commit**

Commit: `evolution-b: rank recurring capability gaps`

**Escalate if:** score depends on an LLM, local ordering, wall clock, or an
unbounded text field.

## Task 3: Suggestion repository, deduplication, and suppression

**Model:** `gpt-5.6-terra`, reasoning `medium`

**Objective:** Persist suggestion projections and append lifecycle evidence
without producing duplicates or re-proposing addressed work.

**Non-goals:** No acceptance/workshop and no notification.

**Files:**
- Create: `hermes_cli/evolution/suggestions.py`
- Modify: `hermes_cli/evolution/ledger.py`
- Create: `tests/hermes_cli/evolution/test_suggestions.py`

**Context pack:** Tasks 1–2; Project A suggestion/generation tables and event
API; design suppression rules.

**Interfaces:**

```python
SuggestionState = Literal["queued", "surfaced", "accepted", "dismissed", "superseded", "addressed"]

def suggestion_id_for(dedupe_key: str) -> str: ...
def evaluate_suggestions(ledger: EvolutionLedger, policy: ObserverPolicy,
                         now: datetime) -> SuggestionEvaluation: ...
def list_suggestions(ledger: EvolutionLedger, *, states: set[str] | None,
                     limit: int, after: str | None) -> list[EvolutionSuggestion]: ...
def mark_surfaced(ledger: EvolutionLedger, suggestion_id: str) -> None: ...
```

- [ ] **Step 1: Write failing dedupe tests**

The same semantic gap across batches and generations keeps one suggestion ID
and appends evidence references. A changed capability/operation/signature gets
a distinct ID.

- [ ] **Step 2: Write failing suppression tests**

Suppress when an accepted attempt, quarantined/active/stable generation, or
newer suggestion explicitly supersedes the same dedupe key. A failed/expired
attempt does not close the underlying suggestion.

- [ ] **Step 3: Run red tests**

Run: `scripts/run_tests.sh tests/hermes_cli/evolution/test_suggestions.py -q`

- [ ] **Step 4: Implement transactional projection updates**

Append an observer event for creation, score/evidence change, surfacing,
supersession, and addressing. Update mutable suggestion projection fields only
inside the same transaction; do not rewrite lifecycle history.

- [ ] **Step 5: Implement cursor pagination**

Cursor is the canonical encoding of `(score, failure_count, suggestion_id)`;
reject malformed cursors and cap limit at 100.

- [ ] **Step 6: Run tests and commit**

Commit: `evolution-b: deduplicate evolution suggestions`

**Escalate if:** a failed attempt erases a suggestion, suppression relies only
on labels, or evidence from another dedupe key is attached.

## Task 4: Observer CLI and `gnothi_seauton` evidence links

**Model:** `gpt-5.6-terra`, reasoning `medium`

**Objective:** Let operators inspect observer state and trace a suggestion to
sanitized organism evidence.

**Non-goals:** No workshop or acceptance.

**Files:**
- Modify: `hermes_cli/evolution/command.py`
- Modify: `hermes_cli/subcommands/evolution.py`
- Modify: `hermes_cli/gnothi/collectors/experience.py`
- Modify: `hermes_cli/gnothi/query.py`
- Create: `tests/hermes_cli/evolution/test_observer_command.py`
- Modify: `tests/hermes_cli/test_gnothi_collectors.py`
- Modify: `tests/hermes_cli/test_gnothi_query.py`

**Context pack:** Task 3 repository; current Gnothi experience collector/query;
design required semantic view “suggestion evidence to changed component”.

- [ ] **Step 1: Write failing command tests**

Cover `evolution suggestions`, `--state`, pagination, `show suggestion`,
`observer status`, missing event file, malformed cursor, and stable JSON free of
absolute paths.

- [ ] **Step 2: Write failing generation-scope link tests**

A suggestion may reference stable/candidate/historical evidence IDs, but no
cross-generation edge may claim a candidate capability is stable.

- [ ] **Step 3: Run red tests**

Run the new command test plus Gnothi collector/query tests.

- [ ] **Step 4: Implement sanitized evidence views**

Expose event/suggestion public IDs, generation ID, capability/component IDs,
counts, score terms, timestamps, and bounded reason codes only.

- [ ] **Step 5: Run tests and commit**

Commit: `evolution-b: expose evidence-backed suggestion queue`

**Escalate if:** a view needs raw log lines or introduces a cross-generation
verified edge.

## Task 5: Fail-soft background observer and out-of-band notices

**Model:** `gpt-5.6-terra`, reasoning `medium`

**Objective:** Run bounded observation after completed turns and surface a
concise proposal without altering chat history.

**Non-goals:** No automatic acceptance, research, or blocking user responses.

**Files:**
- Create: `hermes_cli/evolution/notices.py`
- Modify: `agent/turn_finalizer.py`
- Create: `tests/hermes_cli/evolution/test_observer_notices.py`
- Create: `tests/agent/test_turn_finalizer_evolution_observer.py`
- Modify: `tests/gateway/test_notice_rendering.py`

**Context pack:** Existing `AgentNotice`, CLI/gateway callbacks, finalizer tail,
Tasks 1–4.

**Interfaces:**

```python
def schedule_observer_scan(agent: object) -> None: ...
def next_notice(ledger: EvolutionLedger, policy: ObserverPolicy) -> AgentNotice | None: ...
```

Notice text is exactly bounded to:

```text
Hades ha rilevato un limite ricorrente in <capability_id> e propone
un'evoluzione (suggerimento <suggestion_id>). Usa /autopoiesis
<suggestion_id> per esaminarla.
```

- [ ] **Step 1: Write failing no-chat-mutation tests**

Snapshot `messages` before and after scheduling and assert byte-equivalent
content. Assert no synthetic user/system/assistant message and no prompt/tool
schema rebuild.

- [ ] **Step 2: Write failing rate/failure tests**

One suggestion is surfaced once per material score revision; scans obey the
configured interval; executor saturation drops work; database/log failures
produce no exception and no user-response delay.

- [ ] **Step 3: Run red tests**

Run: `scripts/run_tests.sh tests/hermes_cli/evolution/test_observer_notices.py -q`

- [ ] **Step 4: Implement one process-local bounded scheduler**

Use one daemon worker and an atomic in-flight/rate-limit guard. Capture only
the notice callback and profile identity needed for delivery; do not retain
messages or the whole agent beyond the scan.

- [ ] **Step 5: Bootstrap before the first enabled scan**

When `evolution.enabled` and `evolution.observer.enabled` are true, call Project
A's trusted `ensure_evolution_initialized()` inside the background worker
before opening the ledger. A bootstrap failure is logged safely and ends that
scan; it does not delay or fail the user turn.

- [ ] **Step 6: Add the finalizer seam**

Call scheduling after the response/result is established and wrap the call in
an independent fail-soft guard. Use `agent._emit_notice()` only after the
suggestion transaction commits.

- [ ] **Step 7: Run tests and commit**

Commit: `evolution-b: surface passive evolution proposals`

**Escalate if:** a notice fires mid-stream, repeats every turn, keeps an agent
alive indefinitely, or changes role alternation/prompt bytes.

## Task 6: Privacy, determinism, and real-path Observer gate

**Model:** `gpt-5.6-sol`, reasoning `high`

**Objective:** Independently prove that the Observer is deterministic,
privacy-bounded, non-authoritative, and usable for the future pilot.

**Non-goals:** No implementation of workshop or pilot.

**Files:**
- Create: `tests/integration/test_autopoiesis_observer.py`
- Create: `docs/autopoiesis/observer-v1.md`

No production files are planned in this verification task. A reproduced defect
must become a focused failing regression and a separate repair task before the
gate is rerun.

**Context pack:** All Project B diffs/tests; design Observer/privacy/acceptance
criteria 1–2 and 17–20; no builder/supervisor code.

- [ ] **Step 1: Build one real recurring event stream**

Through the real event emitter, record the same deterministic capability
failure on at least two controlled dates and above threshold. Run import and
evaluation in a temporary real `HERMES_HOME`.

- [ ] **Step 2: Prove idempotence**

Rerun import/evaluation, rotate the log, restart the process, and reverse batch
boundaries. Assert one suggestion ID, identical score, and no duplicate event.

- [ ] **Step 3: Prove non-authority**

Search Project B imports/calls and assert there is no grant issuance, web
operation, candidate workspace, adapter invocation, pointer write, or
generation publication.

- [ ] **Step 4: Seed adversarial privacy fixtures**

Put Unix/Windows/relative/file-URI paths, API-key-shaped strings, prompt text,
and stack content in attempted event inputs. Assert durable records contain
only bounded classes/digests and no seeded value.

- [ ] **Step 5: Run full verification**

```bash
scripts/run_tests.sh tests/hermes_cli/evolution -q
scripts/run_tests.sh tests/integration/test_autopoiesis_foundation.py tests/integration/test_autopoiesis_observer.py -q
scripts/run_tests.sh tests/hermes_cli/test_gnothi_*.py -q
```

- [ ] **Step 6: Document and commit**

Document scoring v1, dedupe identity, eligibility, suppression, notification,
and the exact evidence fields Project C may consume.

Commit: `evolution-b: verify deterministic passive observer`

**Escalate if:** any result changes across replay, private seeded data survives,
or a Project B code path possesses later-stage authority.
