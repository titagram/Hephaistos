# Autopoiesis Project B — Global Telos and Opportunity Observer Design

**Date:** 2026-07-24
**Status:** Approved design, awaiting implementation plan
**Parent design:** `2026-07-22-autopoiesis-mvp-design.md`
**Implemented prerequisite:** Project A through commit `daa4b4727`
**Replaces:** the Project B design assumptions and implementation plan dated
2026-07-23

## Authority and Relationship to Earlier Documents

This document is the authoritative refinement for Project B. It replaces these
earlier Project B assumptions:

- Autopoiesis state is profile-scoped.
- The Observer considers recurring failures as its primary or mandatory gate.
- Project B has no Telos model or Telos Workshop.
- Suggestions belong to one profile.
- A fixed two-day recurrence threshold is required before every proposal.

The rest of the 2026-07-22 Autopoiesis MVP design remains authoritative unless
this document explicitly changes it. Projects C through F must be replanned
after Project B because their inputs now include a global organism identity,
an active Telos digest, and global suggestions.

The existing
`docs/superpowers/plans/2026-07-23-autopoiesis-project-b-observer.md` must not be
executed as written. A new granular implementation plan will replace it after
this specification is reviewed.

## Product Outcome

Project B gives Hades a bounded form of goal-directed meta-observation:

1. Hades has one global identity and one user-approved Telos.
2. All profiles can contribute minimized observations to one global Observer.
3. The Observer recognizes failures, missing capabilities, friction, explicit
   feedback, and evidenced gaps between the current organism and its Telos.
4. Equivalent observations become one global, explainable suggestion.
5. Hades may propose opening an Autopoiesis workshop.
6. Hades cannot research the web, download components, write candidate code,
   activate a generation, or alter its own Telos.

The intended metaphor is:

> One individual, one direction of becoming, different clothes for different
> roles.

Profiles are role/personality overlays. They are not separate organisms and do
not own separate souls, Teloi, Observer queues, or evolutionary lineages.

Project B is successful when two isolated profiles and two isolated backend
project scopes can contribute safe observations to one global suggestion
without sharing raw conversation or project data.

## Foundational Decisions

1. **The organism is global.** Telos, `gnothi_seauton`, Observer, suggestions,
   Autopoiesis lifecycle, active generation, last-known-good generation, and
   lineage belong to one local organism.
2. **Profiles are personas.** A profile may change role, tone, configuration,
   credentials, memory, channels, or enabled tools. It cannot redefine the
   organism's Telos or lineage.
3. **Global ownership does not erase data scope.** A global Observer may
   aggregate minimized facts from project-scoped sources. It cannot merge,
   search, or silently inherit project memories.
4. **Raw evidence stays at the source.** Prompt bodies, transcripts, user
   memory, tool arguments/results, secrets, paths, and backend knowledge remain
   in their profile/project scope.
5. **Telos is user-governed.** Hades may draft or propose a Telos amendment,
   but only an explicit user action can approve, activate, or roll back a Telos
   revision.
6. **Opportunity is broader than failure.** A first-time explicit unmet
   capability can be important even when no exception occurred.
7. **Ranking is deterministic.** Models may classify bounded inputs, draft
   explanations, and suggest possible merges. They cannot alter evidence,
   eligibility gates, ranking terms, or lifecycle authority.
8. **No mandatory extra model call per turn.** The normal capture path uses
   structured runtime facts or a signal emitted by the already-running agent.
   Aggregation, eligibility, and ranking are local and deterministic.
9. **Prompt caching remains sacred.** Active Telos and organism revisions are
   pinned at session creation. They never rewrite an existing conversation's
   prompt or tool schema.
10. **Project B is proposal-only.** It owns observation and user-approved
    Telos definition, not research, construction, promotion, or rollback of
    executable components.
11. **Project A is evolved, not duplicated.** Its ledger and lifecycle
    contracts are migrated to global organism scope. Project B does not create
    a second competing lifecycle database.
12. **Backend routing fails closed.** A missing or ambiguous workspace binding
    never falls back to the most recent/default backend agent or token.

## Scope

### Included

- one durable global organism identity;
- one global, versioned, user-approved Telos;
- a local Telos Workshop reached through `/autopoiesis`;
- a global organism-home resolver independent of the active profile;
- explicit migration/archival of the profile-scoped Project A baseline;
- global `gnothi_seauton` structural revisions with profile facets;
- minimized observation envelopes from all profiles;
- five signal families;
- deterministic opportunity identity, aggregation, eligibility, and ranking;
- one global suggestion registry;
- read-only explanation and history views;
- passive, rate-limited, out-of-band notices;
- pause, resume, status, doctor, and Telos rollback controls;
- fail-soft structured-error and friction capture;
- supplemental cursor-based import of already-redacted local error events;
- explicit backend project-scope references without backend content;
- migration, concurrency, privacy, prompt-cache, and real-path E2E tests.

### Excluded

- web search or network research;
- downloading or installing components;
- building skills, scripts, plugins, or MCP servers;
- changing active/LKG executable generations;
- modifying Hermes/Hades core;
- automatic Telos activation or amendment;
- automatic suggestion acceptance;
- arbitrary semantic access to raw conversations;
- a global cache of backend project memories;
- merging backend projects or workspace bindings;
- fixing or completing the currently paused backend workspace resolver work;
- remote backend changes, deploys, restarts, migrations, or Graph Explorer work;
- continuous model-based introspection after every turn;
- claims of subjective consciousness, desire, or agency.

## Conceptual Model

The organism has three identity layers:

```text
constitution
  non-negotiable safety, authority, privacy, and rollback constraints

telos
  user-approved directions, priorities, tradeoffs, and success indicators

lineage
  immutable history of revisions, generations, approvals, and reversals
```

`gnothi_seauton` describes what the organism currently is and can do. Telos
describes what it should tend to become. The Observer measures evidenced gaps
between the two. Autopoiesis is the governed mechanism that later Projects C
through E use to reduce an approved gap.

The word "wants" in user-facing explanations is shorthand for:

> this opportunity is supported by evidence and is aligned with the active,
> user-approved Telos.

It does not create an autonomous source of authority.

## Scope Model

Four scopes coexist and must never be conflated:

```text
organism scope
  identity, Telos, Observer, suggestions, evolution lineage

profile scope
  persona, role, local memory, credentials, channels, configuration facets

backend project scope
  workspace binding, backend agent/token, authoritative project memory/cache

session scope
  pinned organism revision + Telos revision + profile + workspace route
```

### Organism Scope

Organism state is local to the Hermes installation root returned by
`get_default_hermes_root()`, not the current profile returned by
`get_hermes_home()`.

The organism owns global decisions and generalized learning. It never acts as a
backend project.

### Profile Scope

Profiles remain intentional operational islands for:

- conversational memory;
- secrets and credentials;
- personal or role-specific preferences;
- platform/channel state;
- tool enablement;
- raw logs and transcripts.

A profile can influence how a capability is expressed or whether it is
enabled. A capability acquired by Hades still belongs to the global organism.

### Backend Project Scope

Project knowledge remains authoritative in the backend with a local cache per
workspace binding. The Observer receives only an opaque source-scope reference
and minimized facts.

Any future evidence dereference must:

1. name the exact source reference;
2. resolve the original workspace binding;
3. obtain the matching backend agent and token from that binding;
4. prove that binding, agent, token, project, and cache agree;
5. fail closed when the route is absent or ambiguous.

There is no fallback to a default or most-recent agent inside a linked
workspace.

### Session Scope

At session start Hades pins:

- `organism_id`;
- active Telos digest;
- active executable generation ID;
- `gnothi_seauton` revision digest when a self-model is needed;
- active profile/persona revision;
- workspace route or an explicit unbound state.

Changes become visible only to new sessions. Pinning does not require injecting
the full Telos or organism graph into the system prompt.

## Global Durable Layout

The global state root is:

```text
<default-hermes-root>/organism/          0700
├── identity.json                        0600
├── .organism.lock                       0600
├── evolution/
│   ├── evolution.db                     0600
│   ├── active.json                      0600
│   ├── last-known-good.json             0600
│   └── generations/
├── telos/
│   ├── active.json                      0600
│   ├── last-known-good.json             0600
│   ├── revisions/
│   │   └── <telos-digest>.json          0444
│   └── drafts/                          0700
├── gnothi_seauton/
│   ├── current.json                     0600
│   └── revisions/
├── evidence-brokers/
│   └── <profile-ref>.json               0600
├── archives/
│   └── legacy-profile-state/            digest/index records only
└── wiki/                                generated, rebuildable
```

The conceptual `organism.db` discussed during design is implemented by the
existing Project A `evolution.db`. This avoids two authoritative databases and
allows observation, suggestion, authorization, and later lifecycle evidence to
share one append-only chain.

Canonical structured state is SQLite plus content-addressed JSON. Markdown is
only a generated human-readable projection. Manual edits to generated wiki
pages never change Telos, suggestions, or lifecycle state.

Behavioral settings live under an `autopoiesis` section in the default Hermes
root `config.yaml`, not in `.env`, a profile override, or the database.

## Global Organism Identity

`identity.json` introduces an immutable, opaque `organism_id`. It contains no
username, machine name, absolute path, profile name, backend project ID, or
secret.

Minimum fields:

```python
@dataclass(frozen=True)
class OrganismIdentity:
    schema_version: int
    organism_id: str
    created_at: str
    lineage_root_digest: str
```

`organism_id` is generated once, validated on every open, and bound by future
Telos, generation, and pointer contracts. Copying a profile does not copy or
fork organism identity. Exporting a profile excludes the organism root.

Identity replacement is not a Project B operation.

## Migration from Profile-Scoped Project A State

Project A currently stores lifecycle state beneath the active profile and binds
its baseline pointers to profile identity. Project B must not silently reinterpret
those bytes as global state.

Migration rules:

1. Initialization scans only known Hermes profile roots for coherent legacy
   Project A state.
2. Scanning is read-only and never opens a backend client.
3. If global organism state already exists, legacy state is reported but not
   adopted.
4. If no legacy state exists, initialization creates a fresh global organism
   identity and global baseline.
5. If exactly one coherent baseline-only legacy state exists, initialization
   may propose archival and a new equivalent global baseline.
6. The legacy ledger chain root, pointer digests, and manifest digests are
   recorded as provenance in one global import event.
7. Legacy files remain unchanged in their original profile roots. The global
   `archives/legacy-profile-state` directory stores only a verified manifest of
   their chain/pointer/manifest digests and source-profile opaque ref. Legacy
   rows are never merged record-by-record into the new global ledger.
8. If any legacy state contains a non-baseline attempt or divergent generation,
   automatic adoption is blocked and requires a later explicit migration
   design.
9. Multiple legacy roots are never merged. Byte-identical baseline-only roots
   may be summarized together, but initialization still requires user
   confirmation.
10. Migration stages into a temporary directory, verifies all identities and
    modes, then atomically publishes the global root.
11. Interrupted migration is idempotently resumed or discarded before any
    global pointer is accepted.

Project B introduces v2 organism-bound generation, pointer, and baseline
contracts. Their identity payload uses `organism_id`; it does not use
`profile_id`. An optional `origin_profile_refs` field is non-identity
provenance only. A v1 profile-bound pointer remains evidence about legacy
state and can never become an active global pointer.

## Telos Model

### Telos Contents

An approved Telos revision contains:

```python
@dataclass(frozen=True)
class TelosRevision:
    schema_version: int
    organism_id: str
    parent_digest: str | None
    purpose: str
    desired_traits: tuple["DesiredTrait", ...]
    capability_directions: tuple["CapabilityDirection", ...]
    priorities: tuple["Priority", ...]
    tradeoffs: tuple["Tradeoff", ...]
    prohibitions: tuple["Prohibition", ...]
    proactivity_policy: "ProactivityPolicy"
    success_indicators: tuple["SuccessIndicator", ...]
```

Every nested item has:

- a stable local ID;
- a bounded human-readable statement;
- closed tags used by deterministic alignment;
- an explicit priority;
- optional measurable indicators;
- no prompt, transcript, secret, path, or project-specific fact.

Contract bounds are fixed:

- `purpose` is 1–1000 Unicode scalar values;
- every collection contains 1–32 items, except `tradeoffs` which may be empty;
- IDs match `[a-z][a-z0-9_.-]{0,63}`;
- statements are 1–500 Unicode scalar values;
- tags come from the versioned capability taxonomy and contain at most 16
  values per item;
- priorities are integers 1–5;
- unknown fields and duplicate IDs are rejected.

The canonical digest covers the semantic payload, `organism_id`, and parent
digest. Approval time and display metadata do not alter semantic identity.

### Constitution versus Telos

The constitution is code/config policy and cannot be weakened by a Telos
revision. A Telos prohibition can be stricter than the constitution. It cannot
authorize:

- unapproved network access;
- project-scope leakage;
- autonomous promotion;
- secret acquisition;
- core mutation in the peripheral MVP;
- live-session prompt/tool mutation;
- bypassing approvals or rollback.

A Telos draft that conflicts with the constitution is invalid, not merely
low-ranked.

### Initial Telos Workshop

When no active Telos exists, `/autopoiesis` starts a local workshop that asks
one focused question at a time about:

- Hades's role;
- desired qualities;
- capability directions;
- priority tradeoffs;
- forbidden directions;
- permitted proactivity;
- observable progress.

The conversational model drafts structured input. A deterministic validator
normalizes it and produces:

- the exact canonical payload;
- digest;
- diff from the parent when one exists;
- constitution conflicts;
- a plain-language "what this means" report.

The draft is inert. Activation requires a single-use `UserApprovalReceipt`
issued by the existing host approval surface for the exact Telos digest. Store
methods reject a missing, mismatched, expired, or consumed receipt. A CLI
`approve` invocation creates or resumes that host approval request; neither a
TTY nor a model-controlled shell is sufficient proof. There is no `--yes`,
`--confirm-digest`, environment-variable, or config bypass.

### Amendments and Rollback

Hades may propose an amendment only with:

- the current active digest;
- a bounded evidence set;
- a semantic diff;
- predicted benefits and costs;
- risks and affected success indicators.

Only the user can approve or reject it. Approval creates a new immutable
revision and atomically switches `telos/active.json`.

On first approval, `active.json` and `last-known-good.json` name the same
revision. On amendment, the previously active verified revision becomes
last-known-good before the new active pointer is published. Startup accepts
either pointer only after verifying revision bytes, digest, organism identity,
and matching approval event.

Rollback moves the active pointer to a previously verified revision after
explicit user approval. It appends a rollback event and never deletes later
history.

All changes affect new sessions only.

## Observation Model

### Signal Families

Project B recognizes five closed families:

| Signal | Meaning | Typical source |
|---|---|---|
| `failure` | An operation produced a technical failure or incorrect result | tool/runtime event |
| `capability_absence` | A requested outcome cannot currently be achieved | active agent, user note, Gnothi check |
| `friction` | A task is possible but unnecessarily slow, repetitive, or costly | measured metrics plus feedback |
| `user_feedback` | The user explicitly identifies a weakness or desired improvement | bounded explicit signal |
| `telos_gap` | Current verified capabilities lag an active Telos direction | Gnothi-to-Telos comparison |

Signal family is not the deduplication identity. Several families may support
the same opportunity.

### Provenance Classes

Every observation records exactly one provenance class:

| Provenance | Base confidence | Authority |
|---|---:|---|
| `measured_runtime` | 1.00 | objective bounded metric |
| `explicit_user` | 0.95 | user request/feedback, without storing its text |
| `gnothi_verified` | 0.90 | current organism evidence |
| `structured_tool_result` | 0.80 | bounded tool status/error class |
| `agent_inference` | 0.40 | hypothesis requiring corroboration |
| `legacy_log_import` | 0.30 | supplemental redacted diagnostic |

An `agent_inference` by itself cannot prove a Telos gap or authorize any later
stage.

### ObservationEnvelope

The global ledger accepts only a versioned minimized envelope:

```python
@dataclass(frozen=True)
class ObservationEnvelope:
    schema_version: int
    event_id: str
    organism_id: str
    occurred_at: str
    signal_type: str
    provenance: str
    source_profile_ref: str
    source_project_ref: str | None
    source_session_ref: str | None
    generation_id: str
    gnothi_revision_digest: str | None
    telos_digest: str | None
    capability_key: str
    operation_key: str
    outcome_key: str
    constraint_key: str
    severity: str
    task_impact: str
    retry_count: int
    latency_bucket: str | None
    explicit_user_intent: bool
    recovered: bool
    evidence_refs: tuple[str, ...]
    redaction_status: str
```

All keys are closed taxonomy values or bounded safe identifiers. Free-form
user text is forbidden.

Source refs are installation-local opaque digests. They are not profile names,
workspace paths, backend IDs, repository names, or public handles. Evidence
refs are digests resolved by a profile-local evidence broker in later,
separately authorized workflows.

The envelope validator rejects:

- unknown/future schema versions;
- unbounded strings or collections;
- absolute, relative, Windows, Unix, or `file://` paths;
- URI payloads;
- secret-shaped keys or values;
- prompt/transcript/tool-output fields;
- backend project IDs in display fields;
- invalid generation/Telos/Gnothi bindings;
- timestamps outside the accepted clock-skew window.

Rejected content is not partially stored.

Envelope bounds are fixed:

- serialized canonical JSON is at most 4096 bytes;
- all taxonomy keys match `[a-z][a-z0-9_.-]{0,127}`;
- `retry_count` is 0–1000;
- `evidence_refs` contains at most 16 lowercase SHA-256 refs;
- profile, project, and session refs are fixed-format opaque local IDs;
- severity, impact, latency, provenance, redaction, and signal values are
  closed enums;
- unknown capability taxonomy values normalize to `unknown` and remain
  ineligible until classified.

### Signal Capture

There are four capture paths:

1. **Structured runtime capture.** Existing tool and runtime results emit
   bounded failure, retry, latency, and recovery facts.
2. **Active-agent self-report.** The already-running agent may use the
   Autopoiesis CLI+skill contract to emit a low-authority
   `agent_inference` or a bounded explicit-user signal. This adds no new core
   model tool.
3. **Explicit user note.** `/autopoiesis note` records a structured draft that
   the user reviews before it enters the ledger.
4. **Supplemental local importer.** A profile process tails only its own
   organism event stream with a durable cursor. Existing `errors.log` is
   treated as raw profile-local input: a bounded importer may extract only a
   closed exception/error class and metric buckets after sanitization; it
   never forwards or persists the source line.

The Observer itself never reads raw conversations. An optional existing
background-review model may suggest a bounded envelope, but its result remains
`agent_inference` and is not required for MVP correctness.

### Friction Is a Diagnosis Candidate

User feedback such as "why are you so slow?" does not prove a cause. It creates
a friction observation and begins correlation with:

- elapsed-time buckets;
- retry counts;
- tool-call counts;
- repeated equivalent operations;
- timeouts/cancellations;
- model/provider fallback classes;
- queue or external-service wait classes.

If evidence does not isolate a cause, the opportunity is
`performance.diagnosis`, not an invented implementation fix.

## Deterministic Opportunity Identity and Clustering

The exact opportunity key is a domain-separated digest of:

```text
organism_id
capability_key
operation_key
outcome_key
constraint_key
```

It intentionally excludes:

- profile;
- project;
- session;
- signal family;
- timestamp;
- user wording;
- proposed implementation class.

This allows a failure, explicit complaint, and Gnothi gap from different
profiles/projects to support the same global opportunity.

Project B clustering is deterministic:

1. normalize closed taxonomy aliases;
2. compute the exact opportunity key;
3. attach evidence to that key;
4. keep materially different capability/outcome/constraint tuples separate.

An LLM may suggest that two existing keys are equivalent. It cannot merge them.
Manual merge creates a versioned alias rule and preserves both original keys
and evidence sets. Fuzzy embedding-only clustering is deferred.

## Eligibility Gates

Every eligible opportunity requires:

- at least one valid, safely redacted envelope;
- a current active Telos;
- no matching Telos prohibition;
- no active/pending generation or open accepted attempt already addressing it;
- a supported taxonomy mapping;
- a reproducible deterministic score.

It must then pass at least one gate:

1. explicit user intent plus a verified or explicitly acknowledged capability
   absence;
2. explicit high-impact user feedback;
3. at least three compatible observations from distinct sessions;
4. a Gnothi-verified Telos gap plus at least one operational observation.

Recurrence is therefore important but not universally mandatory.

An abstract `telos_gap` with no operational evidence remains in `observing`
state. One low-confidence agent inference remains in `observing` state.

## Ranking Policy v2

Eligible opportunities receive normalized `[0, 1]` terms:

```text
score =
    0.24 * user_intent
  + 0.20 * telos_alignment
  + 0.16 * impact
  + 0.14 * recurrence
  + 0.10 * confidence
  + 0.08 * reuse
  + 0.04 * (1 - risk)
  + 0.04 * (1 - expected_cost)
```

Rules:

- `user_intent`: explicit unmet request `1.00`; explicit performance/quality
  feedback `.90`; user-reviewed note `.85`; operator-started self-audit `.60`;
  measured runtime signal `.30`; agent inference `.10`.
- `telos_alignment`: exact capability-direction tag `1.00`; desired-trait tag
  only `.75`; success-indicator tag only `.60`; otherwise `.25`. A
  prohibition makes the opportunity ineligible rather than score zero.
- `impact` is the maximum bounded severity/task-impact value:
  `critical=1.00`, `high=.80`, `medium=.50`, `low=.25`, `unknown=.10`.
- `recurrence` is `min(1, valid_observation_count / 10)`.
- `confidence` is
  `min(1, max_provenance_weight + .10 * min(2, additional_provenance_classes)
  + .05 * min(2, additional_distinct_sessions))`.
- `reuse` is
  `min(1, distinct_profile_refs / 4 + distinct_project_refs / 8
  + distinct_operation_keys / 8)`.
- `risk` uses the capability taxonomy's required-authority class:
  `observe=.10`, `local_read=.25`, `local_write=.50`, `device=.65`,
  `network=.70`, `privileged=.95`, `unknown=.85`.
- `expected_cost` uses the same taxonomy:
  `trivial=.10`, `small=.25`, `medium=.50`, `large=.75`, `unknown=.80`.
- Project C may replace risk/cost estimates in a Blueprint, but never rewrite
  the historical Project B score.
- only the final result is rounded to six decimal places.

Sort order is:

1. score descending;
2. explicit user intent descending;
3. impact descending;
4. recurrence descending;
5. risk ascending;
6. stable suggestion ID.

The policy version and all term values are stored with every material score
revision. Replaying a fixed event set under the same policy must produce
byte-identical ordered results.

## Suggestion Registry

Suggestion states are:

```text
observing
  -> eligible
  -> surfaced
  -> accepted | dismissed

observing | eligible | surfaced
  -> superseded | addressed

dismissed
  -> eligible       only after material new evidence
```

`accepted` means the user agreed to open a later Autopoiesis workshop. It does
not grant web, build, or promotion authority.

Each suggestion records:

- stable suggestion ID and opportunity key;
- current state;
- active Telos digest used for alignment;
- evidence refs and counts by signal/provenance;
- score policy/version and terms;
- first/last observed timestamps;
- material score revision;
- suppression/supersession relationships;
- bounded reason codes;
- append-only state events.

User-facing explanations are generated from bounded codes and counts. They do
not quote observations or reveal project/profile identity by default.

## `gnothi_seauton` Integration

The structural organism store becomes global. Profile-specific configuration,
experience, and enabled-capability facets remain labeled inputs rather than
separate organism identities.

Existing profile-scoped Gnothi revisions are not promoted as global truth.
Their chain/current digests are recorded as legacy provenance and left
unchanged. The first global revision is rebuilt from stable/candidate organism
sources with profile facets passed explicitly. That rebuild performs no
backend binding discovery and no backend write.

Project B needs only these semantic views:

- current stable organism capabilities;
- active Telos directions and prohibitions;
- verified absence or partial coverage for a capability key;
- suggestion-to-capability evidence trace;
- profile-facet coverage counts without profile content.

The complete organism graph is not injected into ordinary conversations.
Autopoiesis commands and explicit self-investigation load it on demand.

Project B must not alter backend graph projection, Graph Explorer, canonical
graph query services, or remote graph data. A backend copy of the organism is a
replica only; local verified artifacts remain authoritative.

## Commands and Conversational Surface

The model-facing entry point is the existing skill/slash-command pattern:

```text
/autopoiesis
/autopoiesis suggestions
/autopoiesis show <suggestion-id>
/autopoiesis note
/autopoiesis telos
```

Deterministic operator commands extend the existing namespace:

```text
hermes evolution status
hermes evolution observer status
hermes evolution observer scan
hermes evolution pause
hermes evolution resume
hermes evolution doctor
hermes evolution suggestions
hermes evolution show suggestion <id>
hermes evolution telos status
hermes evolution telos draft
hermes evolution telos approve <digest>
hermes evolution telos history
hermes evolution telos rollback <digest>
```

Exact parser nesting may follow current conventions, but there must be one
canonical operation for each behavior and no duplicate state path.

Read commands support bounded stable JSON. Model-facing skill output is
concise. Approval commands use the user/host approval boundary described
above.

Observer notices use `AgentNotice` or its existing equivalent. They:

- are emitted only after a completed response;
- never inject a synthetic chat message;
- are rate-limited by material suggestion revision;
- identify only suggestion ID, generalized capability, and bounded reason;
- cannot open a workshop or grant authority.

## Configuration

Global user-facing settings live in the default-root `config.yaml`:

```yaml
autopoiesis:
  enabled: false
  observer:
    enabled: true
    scan_interval_seconds: 300
    notice_min_score: 0.65
    max_events_per_scan: 1000
  evidence:
    retention_days: 30
  telos:
    require_interactive_approval: true
```

`autopoiesis.enabled` becomes true only after the user completes initialization
and approves the initial Telos. Pausing sets the global behavior flag; it does
not delete events or alter Telos.

Initialization, status, doctor, and Telos-draft/approval commands remain
available while `autopoiesis.enabled` is false. Observation scans and notices
do not.

Named-profile config may contain persona settings but cannot override global
Autopoiesis authority or thresholds. A misplaced profile-local
`autopoiesis` section is reported by `doctor` and ignored.

## Failure, Recovery, and Concurrency

### Fail-Soft Runtime Rule

Observer capture and scans occur after the user-visible result is established.
Any failure:

- is safely logged;
- may increment a local circuit breaker;
- never fails, delays indefinitely, or rewrites the completed turn;
- never changes chat history, prompt bytes, or tool schemas.

### Append-Only and Idempotent Writes

- Event IDs are stable and unique.
- Importing the same envelope twice produces one durable observation.
- Observation insert and cursor advancement share one transaction.
- Suggestion projection updates and their append-only events share one
  transaction.
- Ranking functions receive events and policy explicitly and perform no I/O,
  clock reads, randomness, config reads, or model calls.
- Multiple profile processes use SQLite WAL and bounded busy handling.
- Global initialization, Telos pointer changes, and lifecycle pointer changes
  serialize under the global organism lock.

### Redaction Failure

If safe minimization cannot be proven:

1. discard the attempted content;
2. record only a bounded `redaction_failed` diagnostic with no source payload;
3. do not count it toward eligibility or ranking;
4. keep normal agent work operational.

### Degraded Modes

| Failure | Behavior |
|---|---|
| Global DB unavailable | Observer disabled for that scan; normal work continues |
| Unknown schema | Read-only blocked status; no automatic migration |
| Invalid Telos pointer | Verify and use `telos/last-known-good.json`; otherwise block Telos and alert |
| No verified Telos | Profile-local events may queue; global eligibility/surfacing remains disabled |
| Unknown score policy | Show score as unverified; do not surface automatically |
| Stale lock | Verify owner/process/age, recover under recorded lock event |
| Projection corruption | Rebuild from append-only observations/events |
| Repeated observer error | Open local circuit breaker and notify once |
| Missing/ambiguous backend route | Do not dereference project evidence |
| Both executable pointers invalid | Preserve Project A stable-base fallback |

Telos rollback and executable generation rollback are separate operations.
Project B implements only the former.

## Backend Project Isolation Dependency

The local `main` worktree currently contains paused, uncommitted changes in:

- `hermes_cli/hades_backend_cmd.py`;
- `hermes_cli/hades_backend_runtime.py`;
- `hermes_cli/hades_backend_status.py`;
- `hermes_cli/hades_backend_sync.py`;
- `plugins/memory/hades_backend/__init__.py`;
- their focused regression tests.

Those changes attempt to replace most-recent/default-agent routing with
workspace-aware binding resolution. They are not yet verified or committed.

Project B rules:

1. use a separate worktree and do not edit those files;
2. do not copy their uncommitted implementation;
3. depend only on a small stable route-resolution contract after that work is
   completed and committed;
4. keep ObservationEnvelope ingestion functional without backend access;
5. use opaque source refs rather than backend IDs;
6. test missing/ambiguous route behavior with local fixtures;
7. do not make a live backend call in Project B;
8. treat any default-agent fallback as a blocking invariant failure.

## Security and Privacy

- Global state is owner-private and rejects hostile symlinks, wrong ownership,
  wrong object types, and unsafe modes.
- The Observer stores no prompt, transcript, user-memory body, secret, raw
  stack, tool argument/result, downloaded content, or absolute/local path.
- Project IDs, binding IDs, profile names, repository names, and session IDs do
  not appear in user-facing suggestion output.
- Evidence brokers cannot be queried by the Observer for raw content.
- Future Project C access to source evidence requires a separate explicit
  approval and exact scope.
- Research authorization never implies local-evidence authorization, and vice
  versa.
- Project B performs no outbound telemetry and no network operation.
- Hades cannot approve its own Telos draft through a model-controlled terminal.
- A suggestion cannot create an authorization grant or lifecycle transition.
- Generated Markdown is untrusted/rebuildable presentation.

The threat model remains the Project A local trusted-host model. Project B does
not claim containment from malicious same-UID code already running inside the
trusted process.

## Prompt Cache and Runtime Cost

The ordinary per-turn path:

- performs no additional model call;
- performs no network operation;
- does not load the organism graph;
- does not inject Telos text into the ongoing conversation;
- does not rebuild the system prompt or tool catalog;
- writes at most one bounded envelope when a signal exists;
- targets a measured p95 local capture latency below 25 ms.

Aggregation and ranking run in a bounded deferred scan. Executor saturation
drops or delays a scan rather than blocking a completed user turn.

The Autopoiesis skill is loaded only through the existing skill mechanism.
Project B adds no universal core model tool.

## Component Boundaries for Lightweight Implementation

The implementation must keep policy, storage, I/O, and presentation separate:

| Unit | Single responsibility |
|---|---|
| `organism_home` | Resolve/validate global root and produce migration plans |
| `organism_identity` | Create/validate immutable organism identity |
| `telos_contract` | Pure schemas, canonicalization, validation, digest |
| `telos_store` | Immutable revisions and active pointer |
| `observation_contract` | Pure envelope schema and validation |
| `experience_bridge` | Profile-local cursor/import into global ledger |
| `observer_policy` | Pure eligibility and scoring |
| `suggestions` | Transactional projection and state repository |
| `observer_service` | Bounded scan orchestration and circuit breaker |
| `notices` | Rate-limited out-of-band projection |
| `command` | Thin deterministic CLI adapters |
| Autopoiesis skill | Conversational workshop and explanations |

No module should both decide policy and perform filesystem/database writes.
No command handler should contain scoring, migration, or canonicalization
logic. Pure contracts must be usable in tests without constructing an agent,
database, profile, backend client, or clock.

The later implementation plan must keep each task small enough for a balanced
model:

- at most one new contract or one state transition family per task;
- normally no more than three production files and three focused test files;
- explicit input/output interfaces before implementation;
- test-first red/green evidence;
- one focused commit;
- an escalation condition instead of open-ended judgment;
- a context pack containing only named files and relevant spec sections.

## Model and Token Policy for Implementation

Project B is deliberately shaped so that nearly all implementation can use the
lightest available balanced model.

### Default Routing

| Work | Model | Reasoning |
|---|---|---|
| fixtures, mapping tables, CLI read views, docs, wiki projections | `gpt-5.6-terra` | `low` |
| pure contracts, repositories, migration steps, scoring, ordinary integration | `gpt-5.6-terra` | `medium` |
| concurrency/crash tests and ambiguous integration repairs | `gpt-5.6-terra` | `high` only when needed |
| independent review of global identity migration, privacy, scope, and approval boundary | `gpt-5.6-sol` | `high` |

At least 90 percent of implementation tasks should be assigned to Terra.
No Sol implementation task is planned by default. Sol reviews narrow
high-consequence boundaries and only implements a repair when its review finds
a concrete defect that cannot be safely decomposed for Terra.

No task uses `xhigh`, `max`, or `ultra` by default.

### Runtime Model Policy

The Observer's capture, eligibility, clustering, ranking, and persistence use
no runtime model.

The active conversation model may classify its own evidenced limitation into a
bounded envelope. That observation has `agent_inference` provenance unless the
user or structured runtime corroborates it.

The Telos Workshop uses the user's configured conversational model to draft
structured content. Deterministic code owns validation, canonical bytes,
digest, diff, and activation authority.

### Escalation to Sol

Escalate only when a task changes or fails one of:

- organism or Telos canonical identity;
- profile-to-global migration correctness;
- user-only Telos approval;
- cross-project evidence isolation;
- append-only/crash-consistent history;
- prompt/tool-schema stability;
- deterministic replay;
- hostile path/secret rejection;
- backend route fail-closed behavior.

The implementation plan must state the exact reason for every Sol assignment.

## Testing Strategy

### Unit and Contract Tests

- global root resolution for default, named-profile, Docker, and custom homes;
- organism identity canonicalization and hostile path rejection;
- legacy baseline scan and migration-plan classification;
- Telos schema, digest, constitution conflicts, diff, approval, and rollback;
- envelope validation and every forbidden field/value class;
- taxonomy normalization and opportunity-key identity;
- all eligibility gates and ineligible combinations;
- hand-calculated ranking fixtures and stable ordering;
- deterministic replay under input permutation and duplicate events;
- suggestion transitions, suppression, dismissal, and re-eligibility;
- cursor rotation, truncation, partial line, malformed event, and crash cases;
- circuit breaker and degraded modes;
- notice rate limits and bounded output.

### Property and Concurrency Tests

- identical semantic input always creates identical identity bytes/digests;
- batch boundaries do not change observation or suggestion results;
- multiple profile writers do not lose or duplicate events;
- projection rebuild equals the pre-corruption projection;
- a Telos pointer never names an unverified revision;
- concurrent approval/rollback produces one coherent active Telos;
- no observer path can write Project A active/LKG executable pointers.

### Integration Tests

Use a real temporary default Hermes root containing:

- two profiles;
- two workspace roots;
- two local backend-binding fixtures;
- one global organism identity;
- one approved Telos;
- distinct raw profile logs;
- compatible and incompatible observations.

Prove:

1. both profiles resolve the same organism/Telos/Observer;
2. raw memories and logs remain distinct;
3. compatible signals produce one global suggestion;
4. the suggestion reports aggregate source counts, not identities/content;
5. an unbound/ambiguous project cannot be dereferenced;
6. no default agent/token fallback occurs;
7. no network or backend client is constructed;
8. pause/resume and process restart preserve history;
9. a new Telos revision affects only a new session;
10. Project A foundation remains coherent and unchanged except for the
    explicit global migration contract.

### End-to-End Scenarios

#### Initial Telos

1. `/autopoiesis` detects no active Telos.
2. The workshop creates a draft.
3. The model cannot activate it.
4. Explicit user approval activates the exact digest.
5. A new session pins it; an existing session remains unchanged.

#### Missing Webcam Capability

1. A profile receives a request requiring video capture.
2. Hades cannot satisfy it and emits a bounded capability-absence signal.
3. No user text or device detail enters the global ledger.
4. A compatible signal from another profile joins the same opportunity.
5. The suggestion becomes eligible through explicit user intent.
6. Hades proposes a future workshop without searching the web.

#### Performance Feedback

1. The user reports that Hades is slow.
2. The explicit feedback is stored without its wording.
3. Runtime metrics are correlated.
4. If no cause is proven, the suggestion is performance diagnosis.
5. The explanation distinguishes evidence from hypothesis.

#### Project Isolation

1. Two workspaces have distinct bindings, tokens, caches, and raw logs.
2. Both produce the same generalized routing-friction signal.
3. One global suggestion is created.
4. No project fact crosses scopes.
5. Ambiguous routing blocks evidence dereference.

### Regression Suites

- Project A lifecycle, authorization, generation store, pointer, locking, and
  reconciliation tests;
- existing `gnothi_seauton` contract/store/query tests;
- profile cloning/export tests proving organism state is not cloned;
- backend route contract tests after the paused resolver work is committed;
- command registry, CLI, gateway help, and desktop slash-extension tests;
- message role alternation and prompt-prefix stability tests.

## Performance Acceptance

- ordinary signal capture makes no model/network call;
- p95 bounded local capture target is below 25 ms on the development host;
- scans enforce event, byte, and wall-time ceilings;
- scan saturation never delays a user-visible result;
- suggestion queries are paginated and capped;
- no whole-log reread occurs after a committed cursor;
- no full organism graph is loaded for an ordinary turn.

Performance tests report measurements without flaky hard CI timing assertions.
Contract tests assert the absence of forbidden calls and unbounded loops.

## Project B Acceptance Criteria

Project B is complete only when all are proven:

1. One immutable global organism identity exists outside every profile.
2. Profile-scoped Project A baseline state is handled by an explicit,
   reversible, evidence-preserving migration.
3. One user-approved global Telos is active.
4. A model cannot approve, activate, or roll back Telos by itself.
5. Existing sessions remain pinned when Telos changes.
6. Both profiles contribute to one Observer and suggestion registry.
7. Five signal families are represented by bounded envelopes.
8. Raw profile/project content never enters global state.
9. A first explicit unmet capability may become eligible without recurrence.
10. Low-confidence self-inference alone cannot become eligible.
11. Recurring compatible signals deterministically deduplicate.
12. Fixed evidence and policy produce byte-identical ranking/order on replay.
13. A Telos prohibition blocks eligibility.
14. Suggestions explain evidence, alignment, uncertainty, risk, and cost
    without inventing causes.
15. Observer notices do not mutate chat history or interrupt a turn.
16. Project B possesses no network, build, grant, generation-publication, or
    executable-pointer authority.
17. Pause, resume, doctor, history, and Telos rollback behave safely.
18. Database/cursor/projection failures degrade without breaking normal Hades
    work.
19. Backend route ambiguity fails closed and no project memories are merged.
20. No remote backend or Graph Explorer surface is modified.
21. Real-path E2E proves the Telos, webcam, performance, and project-isolation
    scenarios.
22. At least 90 percent of implementation tasks use Terra, with Sol limited to
    named review gates or concrete escalated repairs.

## Downstream Contracts for Projects C–F

Project C receives:

- approved global suggestion ID;
- active Telos digest;
- bounded evidence refs and reason codes;
- exact user decision to open a workshop.

Project C must request separate permission for:

- web/network research;
- reading any source-scoped raw evidence.

Project D binds each candidate generation to:

- `organism_id`;
- Blueprint digest;
- active Telos digest used by the Blueprint;
- parent generation.

Project E promotes one global generation for new sessions. Profiles may enable
or hide a global capability according to role and credentials, but they do not
own separate generation lineages.

Project F selects and proves one authentic opportunity under the new v2
eligibility/ranking policy.

## Deferred Work

- fuzzy embedding-based automatic opportunity merging;
- autonomous Telos amendments;
- raw cross-profile conversation analysis;
- remote/global multi-machine organism synchronization;
- merging divergent organism lineages;
- governed core-patch evolution;
- continuous introspection;
- automatic research, build, or promotion;
- subjective or self-originated goals outside the user-approved Telos.
