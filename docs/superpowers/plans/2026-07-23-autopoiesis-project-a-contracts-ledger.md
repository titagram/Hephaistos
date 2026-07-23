# Autopoiesis Project A — Contracts and Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish the immutable identities, state machine, append-only local ledger, scoped authorizations, generation store, baseline pointers, locking, reconciliation, and read-only operator views on which every later project depends.

**Architecture:** A profile-local SQLite ledger is authoritative for lifecycle facts; content-addressed files hold immutable manifests and evidence. Canonical serializers create stable digests. Pointer documents are independently verifiable projections of committed ledger events. Project A can initialize and inspect a baseline but cannot build, canary, promote, or activate a non-baseline overlay.

**Tech Stack:** Python dataclasses and enums, SQLite/WAL, SHA-256, canonical JSON, `fcntl`/Windows-compatible file locking, pytest, argparse.

## Global Constraints

- Read the index plan and design sections “Persistent Model”, “Generation identity”, “Lifecycle State Machine”, “Authorization Model”, and “Failure and Recovery Semantics”.
- Keep schema and state contracts independent of model output.
- `evolution.db` is separate from `state.db`.
- Store timestamps as UTC RFC 3339 with microseconds; never include timestamps in content identities unless the design explicitly lists them as identity fields.
- Project A may create only the empty-overlay baseline and may set both pointers to it during first initialization.
- No operation in this project can activate another generation.
- The evolution root rejects static symlink, owner, type, and private-mode violations. Inode/descriptor checks are defense-in-depth; malicious same-UID or in-process host code is outside the user-approved MVP filesystem threat model, so later candidate execution must not inherit trusted host filesystem authority.

---

## File and Module Map

| Path | Responsibility |
|---|---|
| `hermes_cli/evolution/contract.py` | IDs, canonical JSON, digests, bounded text, shared records |
| `hermes_cli/evolution/state_machine.py` | Allowed attempt transitions and actor/authorization requirements |
| `hermes_cli/evolution/ledger.py` | Schema v2, atomic v1 migration, transactions, append-only events, typed repository reads |
| `hermes_cli/evolution/authorization.py` | Grant requests, issue/consume/expire/deny checks |
| `hermes_cli/evolution/manifest.py` | Stable-base and generation manifest validation and identity |
| `hermes_cli/evolution/store.py` | Private roots, immutable publication, baseline generation |
| `hermes_cli/evolution/pointers.py` | Pointer payload/digest validation and atomic file replacement |
| `hermes_cli/evolution/locking.py` | Profile lifecycle lock |
| `hermes_cli/evolution/reconcile.py` | Startup/read-path coherence and baseline fallback |
| `hermes_cli/evolution/bootstrap.py` | Idempotent host-owned first-use baseline initialization |
| `hermes_cli/evolution/command.py` | `init`, `status`, `history`, and read-only `show` handlers |
| `hermes_cli/subcommands/evolution.py` | `argparse` registration |
| `tests/hermes_cli/evolution/` | Project A unit, contract, concurrency, and recovery tests |

## Task 1: Canonical contracts and lifecycle transition table

**Model:** `gpt-5.6-sol`, reasoning `high`

**Objective:** Make identity bytes and lifecycle legality explicit before persistence exists.

**Non-goals:** No filesystem, SQLite, approval prompt, generation materialization, or CLI.

**Files:**
- Create: `hermes_cli/evolution/__init__.py`
- Create: `hermes_cli/evolution/contract.py`
- Create: `hermes_cli/evolution/state_machine.py`
- Create: `tests/hermes_cli/evolution/__init__.py`
- Create: `tests/hermes_cli/evolution/test_contract.py`
- Create: `tests/hermes_cli/evolution/test_state_machine.py`

**Context pack:** Index interfaces; design “Generation identity” and “Lifecycle State Machine”; `hermes_cli/gnothi/contract.py`; no other files.

**Interfaces:**

```python
def canonical_json_bytes(value: object) -> bytes: ...
def sha256_digest(data: bytes) -> str: ...
def content_digest(value: object, *, domain: str) -> str: ...
def bounded_reason(value: object, *, limit: int = 512) -> str: ...
def require_relative_posix_path(value: object) -> str: ...
def require_digest(value: object) -> str: ...

@dataclass(frozen=True)
class TransitionRequest:
    attempt_id: str
    prior_state: AttemptState
    next_state: AttemptState
    actor: str
    input_digests: tuple[str, ...]
    authorization_id: str | None
    reason: str

def validate_transition(request: TransitionRequest) -> None: ...
```

The canonical digest framing is exact:

```python
def content_digest(value: object, *, domain: str) -> str:
    framed = domain.encode("ascii") + b"\0" + canonical_json_bytes(value)
    return hashlib.sha256(framed).hexdigest()
```

- [ ] **Step 1: Write failing canonicalization tests**

Test equal mappings with different insertion order, Unicode preservation,
rejection of NaN/Infinity, rejection of non-string mapping keys, digest domain
separation, strict lowercase 64-character digest validation, and rejection of
absolute/drive/UNC/URI/empty/`..` paths.

- [ ] **Step 2: Write the complete transition matrix as parametrized failing tests**

Assert the normal path and every terminal/lateral outcome from the design.
Assert that `building`, `quarantined`, `canary_running`, `promotion_ready`,
`active`, `stable`, and `rolled_back` require their designated actor and that
the three approval-bound entries reject a missing authorization ID.

- [ ] **Step 3: Run red tests**

Run: `scripts/run_tests.sh tests/hermes_cli/evolution/test_contract.py tests/hermes_cli/evolution/test_state_machine.py -q`

Expected: import failures because both modules are absent.

- [ ] **Step 4: Implement canonical serialization and validators**

Use `json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False,
allow_nan=False)` and explicitly walk the input to reject bytes, sets,
non-string keys, non-finite floats, and unsupported object instances before
encoding.

- [ ] **Step 5: Implement a data-driven transition policy**

Use one immutable mapping from `(prior_state, next_state)` to required actor
and authorization kind. Do not implement a permissive default. Raise a typed
`EvolutionContractError` with a bounded code, not raw input.

- [ ] **Step 6: Run green tests and commit**

Run the red command again, then `git diff --check`.

Commit: `evolution-a: define canonical lifecycle contracts`

**Escalate if:** equivalent Python values hash differently, a transition needs
model judgment, or a terminal state can re-enter the normal path.

## Task 2: Schema-v1 append-only ledger

**Model:** `gpt-5.6-terra`, reasoning `medium`

**Objective:** Persist lifecycle entities and events transactionally without
allowing event mutation.

**Non-goals:** No authorization decisions, pointer files, component execution,
or candidate artifacts.

**Files:**
- Create: `hermes_cli/evolution/ledger.py`
- Create: `tests/hermes_cli/evolution/test_ledger.py`
- Create: `tests/hermes_cli/evolution/test_ledger_migrations.py`

**User-approved A2 review scope addition:** Task 2 may also modify
`docs/superpowers/specs/2026-07-22-autopoiesis-mvp-design.md`,
`docs/superpowers/plans/2026-07-23-autopoiesis-mvp-index.md`, and this Project A
plan solely to record the local trust boundary and its Project D/E isolation
obligation.

**Context pack:** Task 1 files/commit; design “Evolution ledger”; `hermes_cli/engineering_review/runs.py` private-path patterns; SQLite initialization portions of `hermes_state.py`.

**Schema v1:** create `schema_version`, `attempts`, `suggestions`,
`suggestion_evidence`, `blueprints`, `authorization_requests`,
`authorization_grants`, `candidates`, `generations`, `generation_components`,
`canary_runs`, `promotion_reports`, and `lifecycle_events`. Every table uses
text primary IDs; digests have `CHECK(length(...) = 64)`; states use closed
`CHECK` sets. `lifecycle_events` contains:

```sql
event_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
event_id TEXT NOT NULL UNIQUE,
attempt_id TEXT,
generation_id TEXT,
event_type TEXT NOT NULL,
prior_state TEXT,
next_state TEXT,
actor TEXT NOT NULL,
input_digests_json TEXT NOT NULL,
authorization_id TEXT,
reason_code TEXT NOT NULL,
reason_summary TEXT NOT NULL,
created_at TEXT NOT NULL,
previous_event_digest TEXT,
event_digest TEXT NOT NULL UNIQUE
```

Add SQLite triggers that abort `UPDATE` or `DELETE` on `lifecycle_events` and
`authorization_grants`.

**Interfaces:**

```python
class EvolutionLedger:
    def __init__(self, path: Path | None = None): ...
    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]: ...
    def append_event(self, event: LifecycleEvent) -> StoredEvent: ...
    def create_attempt(self, source_kind: str, source_ref: str) -> str: ...
    def transition(self, request: TransitionRequest) -> StoredEvent: ...
    def history(self, *, limit: int = 100, after: int | None = None) -> list[StoredEvent]: ...
    def verify_chain(self) -> list[str]: ...
```

- [ ] **Step 1: Write failing schema tests**

Assert new and reopened databases report schema version 1, WAL is requested
with safe fallback, foreign keys are on, every required table exists, and
private parent/file modes are `0700`/`0600` on POSIX.

- [ ] **Step 2: Write failing append-only and hash-chain tests**

Append two events and assert the second references the first digest. Direct
`UPDATE` and `DELETE` must raise `sqlite3.IntegrityError`. Through controlled
test-only SQLite setup, change a persisted event and assert the public
zero-argument `verify_chain()` reports the exact sequence.

- [ ] **Step 3: Run red tests**

Run: `scripts/run_tests.sh tests/hermes_cli/evolution/test_ledger.py tests/hermes_cli/evolution/test_ledger_migrations.py -q`

- [ ] **Step 4: Implement private path initialization and idempotent schema creation**

Resolve the root through `get_hermes_home() / "evolution"`; reject symlinked,
wrong-owner, or non-private existing roots rather than chmodding an
attacker-controlled path. Retain and revalidate path identity around connect.
Where descriptor introspection or `dir_fd` is unavailable, use a portable
`lstat`/revalidation fallback that preserves static owner, type, mode, and
symlink rejection and converts unsupported platform behavior into bounded
`EvolutionLedgerError` codes. Descriptor correlation is defense-in-depth, not
proof against malicious same-UID or in-process host code.

- [ ] **Step 5: Implement explicit `BEGIN IMMEDIATE` transactions and event hash chaining**

Build event identity from canonical bounded fields plus
`previous_event_digest`. Compute state update and event append in the same
transaction. Never interpolate SQL identifiers from caller input.

- [ ] **Step 6: Implement migration behavior**

Version 1 may initialize an empty DB. A future/unknown version fails closed
with `EvolutionLedgerError("unsupported_schema_version")`; it must not be
rewritten or downgraded.

- [ ] **Step 7: Run tests and commit**

Run both tests plus Task 1 tests.

Commit: `evolution-a: add append-only lifecycle ledger`

**Escalate if:** an event can be changed, two writers can append with a broken
chain, or a malformed existing DB is silently recreated.

## Task 3: Digest-bound authorization records

**Model:** `gpt-5.6-sol`, reasoning `high`

**Objective:** Implement separate, expiring, single-use research, build, and
promotion grants bound to immutable subjects and closed scopes.

**Non-goals:** No UI prompt, web call, build, canary, or pointer switch.

**Files:**
- Create: `hermes_cli/evolution/authorization.py`
- Modify: `hermes_cli/evolution/ledger.py`
- Create: `tests/hermes_cli/evolution/test_authorization.py`
- Modify: `tests/hermes_cli/evolution/test_ledger_migrations.py`
- Modify: `tests/hermes_cli/evolution/test_ledger.py` only to update its
  direct-SQL immutability fixture to a valid schema-v2 grant

**Context pack:** Tasks 1–2; design “Authorization Model”; relevant
`tools/approval.py` elicitation API only.

**Interfaces:**

```python
def create_authorization_request(
    ledger: EvolutionLedger,
    *,
    attempt_id: str,
    kind: GrantKind,
    subject_digest: str,
    scope: Mapping[str, object],
    ttl_seconds: int,
) -> AuthorizationRequest: ...

def issue_grant(
    ledger: EvolutionLedger,
    *,
    request_id: str,
    approved_by: str,
    confirmation_digest: str,
) -> AuthorizationGrant: ...

def consume_grant(
    ledger: EvolutionLedger,
    *,
    grant_id: str,
    expected_kind: GrantKind,
    expected_subject_digest: str,
    required_scope: Mapping[str, object],
) -> AuthorizationGrant: ...
```

The confirmation digest is
`content_digest(request.canonical_payload(), domain="hades-evolution-authorization-request-v1")`.

- [ ] **Step 1: Write failing authorization matrix tests**

Cover kind mismatch, wrong subject digest, expired request, expired grant,
denial, double issue, double consumption, broader required scope, immutable
scope after issue, and concurrent attempts to consume one grant.

- [ ] **Step 2: Run red tests**

Run: `scripts/run_tests.sh tests/hermes_cli/evolution/test_authorization.py -q`

- [ ] **Step 3: Implement closed scope validators**

Research accepts only `source_classes`, `domains`, `operations=["search","retrieve"]`,
and duration. Build accepts only enumerated component/source/dependency
families, workspace class, isolation policy, side effects, and resource limits.
Promotion accepts exactly generation ID, report digest, expected active ID,
expected lifecycle sequence, and operation `switch_active`.

- [ ] **Step 4: Implement issue and consumption transactions**

Migrate a valid schema-v1 ledger atomically to schema v2 before writable use.
Preserve every legitimate A2 row, but fail closed if a placeholder
authorization row cannot be migrated without inventing authority. Update the
semantic schema fingerprint and continue rejecting future versions.

Keep request, decision, grant, and consumption facts immutable. Represent
single-use consumption as a unique append-only fact keyed by `grant_id`; never
update the issued grant row. Use compare-and-set SQL predicates
(`consumption IS NULL`, no denial, `expires_at > now`) whose insert row count is
exactly one. Append each matching lifecycle authorization event in the same
transaction.

Read the issue/consume clock only after `BEGIN IMMEDIATE` acquires the write
lock. Validate all nested scope symbols and actor identifiers with the same
privacy-safe symbolic policy before persistence. Enforce exact
request-to-decision-to-grant coherence in SQLite so direct SQL cannot invent or
reshape authority, and validate canonical UUID lookup identifiers before any
query. After acquiring the migration lock, re-read the schema version so two
connections that independently preflight v1 can migrate idempotently.

- [ ] **Step 5: Prove concurrency behavior**

Use two independent ledger connections and a barrier; exactly one consumer
succeeds and one receives `grant_unavailable`.

- [ ] **Step 6: Run tests and commit**

Run authorization plus ledger tests.

Commit: `evolution-a: bind lifecycle authority to immutable digests`

**Escalate if:** scope comparison is based on string containment, consumption
is separate from the authorized transition, or a model-produced statement can
substitute for an issued grant.

## Task 4: Generation manifest and immutable store

**Model:** `gpt-5.6-terra`, reasoning `medium`

**Objective:** Validate generation identity manifests and publish byte-identical
directories idempotently.

**Non-goals:** No acquisition, execution, builder orchestration, or activation.

**Files:**
- Create: `hermes_cli/evolution/manifest.py`
- Create: `hermes_cli/evolution/store.py`
- Create: `tests/hermes_cli/evolution/test_manifest.py`
- Create: `tests/hermes_cli/evolution/test_store.py`

**Context pack:** Task 1 canonical functions; design “Generation manifest” and
“Baseline generation”; `utils.atomic_replace`; private-file helpers from
engineering review.

**Interfaces:**

```python
def identity_payload(manifest: Mapping[str, object]) -> dict[str, object]: ...
def generation_id_for(manifest: Mapping[str, object]) -> str: ...
def validate_manifest(manifest: Mapping[str, object], root: Path | None = None) -> None: ...

class GenerationStore:
    def initialize_baseline(self, stable_base: StableBaseIdentity) -> PublishedGeneration: ...
    def publish_staged(self, staged_root: Path, manifest: Mapping[str, object]) -> PublishedGeneration: ...
    def verify(self, generation_id: str) -> PublishedGeneration: ...
```

- [ ] **Step 1: Write failing manifest tests**

Reject mutable attestations in the identity payload, absolute/local paths,
`file://`, secret-shaped keys/values, missing license/author/source
provenance, duplicate logical IDs, component digest mismatch, unsupported
component class, incompatible schema, and generation ID mismatch.

- [ ] **Step 2: Write failing store tests**

Assert equivalent staged bytes converge on one generation ID, an existing ID
with different bytes fails, staging never appears as a generation, a failed
rename leaves no partial generation, and verified generation files cannot be
opened writable on POSIX.

- [ ] **Step 3: Run red tests**

Run: `scripts/run_tests.sh tests/hermes_cli/evolution/test_manifest.py tests/hermes_cli/evolution/test_store.py -q`

- [ ] **Step 4: Implement manifest identity projection**

Exclude only `generation_id`, `created_at`, and mutable attestations from
identity. Include component/lockfile digests, parent, Blueprint digest, stable
base identity, compatibility range, capabilities, invariants, canary policy,
resource ceilings, expected organism diff, and rollback plan.

- [ ] **Step 5: Implement stage-verify-rename-reopen publication**

Copy only validated relative paths into a private sibling temporary directory;
fsync files and directories; atomically rename to the digest directory; reopen
and rehash every declared byte before returning.

- [ ] **Step 6: Implement empty-overlay baseline**

Its manifest has no components, binds the installed Hermes version plus Git
commit when available and compatibility version, and uses the same
publication path as later generations.

- [ ] **Step 7: Run tests and commit**

Commit: `evolution-a: add immutable overlay generation store`

**Escalate if:** generation identity depends on mtime/order/absolute location,
publication can expose partial bytes, or baseline bypasses normal validation.

## Task 5: Pointer documents and baseline initialization

**Model:** `gpt-5.6-terra`, reasoning `medium`

**Objective:** Create and verify baseline `active.json` and
`last-known-good.json` pointer documents.

**Non-goals:** No promotion or rollback operation; Project E owns non-baseline
switching.

**Files:**
- Create: `hermes_cli/evolution/pointers.py`
- Modify: `hermes_cli/evolution/store.py`
- Create: `tests/hermes_cli/evolution/test_pointers.py`

**Context pack:** Tasks 1, 2, and 4; design “Pointer documents” and baseline
initialization.

**Interfaces:**

```python
def pointer_integrity_digest(payload: Mapping[str, object], event_digest: str) -> str: ...
def validate_pointer(document: Mapping[str, object], ledger: EvolutionLedger,
                     store: GenerationStore) -> PointerDocument: ...
def initialize_baseline_pointers(ledger: EvolutionLedger, store: GenerationStore,
                                 baseline: PublishedGeneration) -> tuple[PointerDocument, PointerDocument]: ...
def atomic_write_pointer(path: Path, document: PointerDocument) -> None: ...
```

- [ ] **Step 1: Write failing integrity and tamper tests**

Change each of profile ID, generation ID, manifest digest, sequence, timestamp,
event digest, and integrity digest independently and assert rejection.

- [ ] **Step 2: Write failing atomic-write tests**

Inject failures before file fsync, before rename, and before directory fsync.
The prior valid document must remain readable or reconciliation must see a
clearly absent pointer, never truncated JSON.

- [ ] **Step 3: Run red tests**

Run: `scripts/run_tests.sh tests/hermes_cli/evolution/test_pointers.py -q`

- [ ] **Step 4: Implement canonical pointer payload and domain-framed digest**

Use the exact design domain string
`b"hades-evolution-pointer-v1\0" + canonical_payload + ledger_event_digest.encode("ascii")`.

- [ ] **Step 5: Implement idempotent first initialization**

Append one committed baseline-designation event, write both pointers at the
same lifecycle sequence, and on repeat verify rather than rewrite. Refuse to
replace an existing coherent non-baseline pointer.

- [ ] **Step 6: Run tests and commit**

Commit: `evolution-a: initialize verified baseline pointers`

**Escalate if:** pointer verification trusts file content without the ledger or
store, or baseline init can overwrite an existing active generation.

## Task 6: Lifecycle lock and reconciliation

**Model:** `gpt-5.6-sol`, reasoning `high`

**Objective:** Serialize lifecycle mutations and deterministically recover or
disable overlays when baseline state is incoherent.

**Non-goals:** No promotion recovery; this task covers initialization and
read-path coherence seams that Project E will extend.

**Files:**
- Create: `hermes_cli/evolution/locking.py`
- Create: `hermes_cli/evolution/reconcile.py`
- Create: `tests/hermes_cli/evolution/test_locking.py`
- Create: `tests/hermes_cli/evolution/test_reconcile.py`

**Context pack:** Tasks 2, 4, and 5; design “Failure and Recovery Semantics”;
`hermes_cli/engineering_review/authority.py` socket/path ownership checks.

**Interfaces:**

```python
@contextmanager
def lifecycle_lock(*, timeout_seconds: float = 30.0) -> Iterator[LifecycleLease]: ...

@dataclass(frozen=True)
class ReconciliationResult:
    status: Literal["coherent", "restored_lkg", "base_only", "blocked"]
    active: PointerDocument | None
    last_known_good: PointerDocument | None
    overlay_enabled: bool
    diagnostics: tuple[str, ...]

def reconcile_evolution_state(*, repair: bool) -> ReconciliationResult: ...
```

- [ ] **Step 1: Write failing mutual-exclusion and stale-owner tests**

Two processes must not hold the lease concurrently. A crashed process must
release through kernel lock semantics; deleting a lockfile must not permit a
second lease while the inode remains locked.

- [ ] **Step 2: Write the reconciliation truth table**

Cover coherent pointers; missing active with valid LKG; invalid active with
valid LKG; valid active with invalid LKG; both invalid; pointer to missing
generation; wrong manifest; ledger chain failure; uncommitted event; future
schema. Read-only mode reports but never repairs.

- [ ] **Step 3: Run red tests**

Run: `scripts/run_tests.sh tests/hermes_cli/evolution/test_locking.py tests/hermes_cli/evolution/test_reconcile.py -q`

- [ ] **Step 4: Implement inode-held OS locking**

Use `fcntl.flock` on POSIX and the repository's supported Windows lock
primitive when present. Validate private owner/mode before opening. Write only
bounded owner diagnostics; correctness comes from the held descriptor, not
PID-file liveness.

- [ ] **Step 5: Implement fail-closed reconciliation**

Never infer success from newest mtime. Accept only ledger/store/pointer
coherence. When neither pointer proves a generation, return stable-base-only
with overlays disabled and append a supervisor recovery diagnostic only when
`repair=True`.

- [ ] **Step 6: Run concurrency/recovery tests repeatedly**

Run the task command ten times in a shell loop and then all Project A tests.

- [ ] **Step 7: Commit**

Commit: `evolution-a: serialize and reconcile lifecycle state`

**Escalate if:** recovery relies on PID existence, repairs without the lock, or
chooses a generation using timestamps rather than committed evidence.

## Task 7: Config and deterministic operator read surface

**Model:** `gpt-5.6-terra`, reasoning `medium`

**Objective:** Expose safe initialization, status, history, and typed show
commands without giving the command layer hidden lifecycle authority.

**Non-goals:** No workshop, approval issue, build, canary, promotion, or
rollback command yet.

**Files:**
- Modify: `hermes_cli/config.py`
- Create: `hermes_cli/evolution/command.py`
- Create: `hermes_cli/evolution/bootstrap.py`
- Create: `hermes_cli/subcommands/evolution.py`
- Modify: `hermes_cli/main.py`
- Create: `tests/hermes_cli/evolution/test_command.py`
- Create: `tests/hermes_cli/test_evolution_subcommand.py`

**Context pack:** Project A public interfaces; `hermes_cli/hades_gnothi_cmd.py`;
engineering review command/parser pattern; config normalization helpers.

**Config defaults:**

```yaml
evolution:
  enabled: true
  observer:
    enabled: true
    recurrence_threshold: 3
    scan_interval_seconds: 300
    notice_min_score: 0.65
  authorization:
    research_ttl_seconds: 1800
    build_ttl_seconds: 1800
    promotion_ttl_seconds: 900
  retention:
    workspaces: 10
    evidence_days: 30
```

- [ ] **Step 1: Write failing parser and JSON contract tests**

Cover `hermes evolution init|status|history|show
suggestion|blueprint|generation|report`, `--json`, bounded pagination, missing
records, broken state, and no database creation for read-only `status` unless
`init` is invoked.

- [ ] **Step 2: Run red tests**

Run: `scripts/run_tests.sh tests/hermes_cli/evolution/test_command.py -q`

- [ ] **Step 3: Add defaults and strict normalization**

Invalid booleans, negative limits, and out-of-range scores fall back to
documented defaults. Do not add a `HERMES_*` behavioral environment variable.

- [ ] **Step 4: Implement command handlers**

`init` obtains the lifecycle lock and is the only mutating command in Project
A. Read commands open the ledger read-only when it exists and emit stable
records with public IDs and bounded diagnostics.

- [ ] **Step 5: Implement idempotent first-use bootstrap**

`ensure_evolution_initialized()` may be called by a trusted local host before
the first Observer scan or new-session resolution. It acquires the lifecycle
lock, initializes only when no evolution state exists, and delegates to the
same baseline path as `init`. It never replaces or repairs an existing state;
those outcomes belong to reconciliation. Unit tests call this seam twice and
concurrently and still observe one baseline/event.

- [ ] **Step 6: Register the parser**

Register one top-level `evolution` subparser in `hermes_cli/main.py`; keep the
handler lazy-imported. Do not modify backend parsers.

- [ ] **Step 7: Run tests and commit**

Run command, config, and all Project A tests.

Commit: `evolution-a: expose baseline lifecycle status`

**Escalate if:** `status` repairs state implicitly, JSON contains an absolute
path/secret, or parser registration imports heavy candidate code at startup.

## Task 8: Project A real-path verification and handoff

**Model:** `gpt-5.6-terra`, reasoning `high`

**Objective:** Prove the foundation works against a temporary real
`HERMES_HOME` and package the exact contracts for Project B.

**Non-goals:** No simulated candidate and no promotion.

**Files:**
- Create: `tests/integration/test_autopoiesis_foundation.py`
- Create: `docs/autopoiesis/contracts-v1.md`

No production files are planned in this verification task. A reproduced defect
must first be reduced to a focused failing test and inserted as a separate
repair task immediately before this gate.

**Context pack:** Project A commits; index acceptance constraints; no Project
B–F source.

- [ ] **Step 1: Write a real-home integration test**

Invoke the real CLI entry point in subprocesses to initialize, reopen, query,
and concurrently call `init`. Assert one baseline generation, one coherent
event chain, both pointers equal, private modes, no absolute paths in ledger
text, and byte-identical output on read.

- [ ] **Step 2: Add interruption fixtures**

Interrupt pointer initialization at each injected boundary and rerun `init`
under the real lifecycle lock. Assert recovery or explicit base-only failure,
never a second baseline identity.

- [ ] **Step 3: Run the focused and regression suites**

```bash
scripts/run_tests.sh tests/hermes_cli/evolution -q
scripts/run_tests.sh tests/integration/test_autopoiesis_foundation.py -q
scripts/run_tests.sh tests/hermes_cli/test_gnothi_*.py -q
```

- [ ] **Step 4: Document the exact v1 contracts**

Record schema version, canonical digest domains, allowed transitions, grant
scope keys, directory layout, pointer fields, and Project B repository calls.
Do not describe unimplemented builder or promotion behavior as available.

- [ ] **Step 5: Inspect for forbidden content**

Create representative records, run `strings`/SQLite queries over
`evolution.db`, and assert fixture secrets, workspace absolute paths, prompt
text, and raw failure output are absent.

- [ ] **Step 6: Commit and hand off**

Commit: `evolution-a: verify local lifecycle foundation`

Handoff must name the green commit hashes for Tasks 1–8 and state that only
baseline activation exists.

**Escalate if:** the subprocess behavior differs from unit tests, a crash needs
manual file deletion, or forbidden content enters durable state.
