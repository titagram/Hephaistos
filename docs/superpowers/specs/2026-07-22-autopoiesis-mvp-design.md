# Autopoiesis MVP Design

**Date:** 2026-07-22
**Status:** Approved design with implementation plans ready
**Parent design:** `2026-07-11-gnothi-seauton-autopoiesis-design.md`
**Prerequisite:** an operational `gnothi_seauton` organism model
**Implementation plan:** `../plans/2026-07-23-autopoiesis-mvp-index.md`

## Summary

Autopoiesis turns Hades self-improvement into a governed ability to create new
peripheral pieces of itself. Hades may observe recurring capability gaps,
propose an evolution, research possible solutions after explicit permission,
build an isolated candidate, prove it with a canary, and ask the user whether
to promote it. A supervisor outside the candidate owns activation and
rollback.

The MVP supports the complete peripheral extension surface:

- skills;
- extension packs and scripts;
- local plugins;
- MCP server configuration.

It does not permit Hades to patch its own core. Every evolution is an immutable
overlay generation layered on the stable installation. Each active session is
pinned to one generation, preserving the byte-stability of its system prompt
and tool schema.

The MVP is successful only after one authentic, locally reproducible evolution
travels through the entire lifecycle and a controlled critical failure proves
automatic rollback to the last-known-good generation.

## Relationship to the Parent Design

The parent design established the complete `gnothi_seauton` and autopoiesis
roadmap. This document is the authoritative refinement for the Autopoiesis MVP.
Where the two differ, this document governs the MVP:

- candidate core patches are deferred;
- candidates are peripheral overlay generations, not complete installation
  copies;
- three separate approvals govern research, build, and promotion;
- the canary must include one real repeatable task, not only synthetic tasks;
- version identity belongs to a dedicated evolution ledger and content-derived
  generation ID, not to Git, filesystem checkpoints, or backup archives.

Existing Hades mechanisms remain supporting infrastructure:

- `gnothi_seauton` supplies evidence-backed self-knowledge and semantic diffs;
- structured logs and the project logbook supply bounded operational evidence;
- skill quarantine and authoring flows are reused where their contracts fit;
- worktrees and isolated environments support candidate construction;
- checkpoints and backups remain disaster-recovery aids;
- the existing approval system supplies interactive consent primitives;
- canonical graph artifacts remain the source-aware graph foundation.

None of those mechanisms individually represents an evolution generation.

## Product Outcome

The desired interaction is:

```text
repeated evidenced limitation
  -> queued EvolutionSuggestion
  -> user accepts or starts /autopoiesis directly
  -> scoped research approval
  -> collaborative workshop
  -> immutable EvolutionBlueprint
  -> build approval
  -> quarantined overlay generation
  -> external canary and organism diff
  -> promotion report
  -> explicit promotion approval
  -> atomic activation for new sessions
  -> healthy observation window
  -> stable generation or automatic hard-failure rollback
```

Hades must always be able to answer:

1. Why did it propose this change?
2. What exactly did the user authorize?
3. Which sources and artifacts became part of the candidate?
4. What changed in the organism?
5. Which evidence justified promotion?
6. Which generation is active and which one is the recovery point?
7. Why did an automatic rollback occur?

## Foundational Decisions

1. **Peripheral-only MVP.** Skills, extension packs/scripts, local plugins,
   and MCP configuration are in scope. Core patches are not.
2. **Immutable overlay generations.** The stable installation is not copied or
   edited in place. A generation contains only versioned peripheral overlays.
3. **Dedicated identity.** A generation is identified by the SHA-256 digest of
   its canonical identity manifest and component digests.
4. **Git is provenance, not identity.** Commits and worktrees may be recorded,
   but they do not define which organism generation is active.
5. **Three approvals.** Research, construction, and promotion are independent,
   expiring, single-use authorizations bound to immutable inputs.
6. **Proactive observation, passive authority.** The Observer may propose what
   Hades wants to improve, but cannot research, build, or promote by itself.
7. **External rollback ownership.** The candidate cannot declare itself
   healthy, activate itself, or suppress rollback.
8. **New-session activation.** Promotion never mutates the toolset or prompt of
   an existing conversation.
9. **Fail closed.** Missing evidence, skipped tests, incoherent state, or
   material deviations cannot be interpreted as success.
10. **One authentic proof.** The first release demonstrates one real recurring
    gap end-to-end while providing adapters for the entire peripheral surface.

## Scope

### Included

- bounded structured experience events;
- a deduplicated and ranked evolution suggestion queue;
- `/autopoiesis` and deterministic CLI lifecycle commands;
- collaborative Blueprint creation;
- scoped web-research authorization;
- candidate build authorization;
- isolated construction and dependency locking;
- source, license, provenance, and digest capture;
- adapters for all four peripheral component classes;
- immutable generation storage;
- candidate-scoped `gnothi_seauton` revisions and semantic diffs;
- external canary execution;
- explicit promotion approval;
- atomic active and last-known-good pointers;
- hard-failure automatic rollback;
- append-only lifecycle evidence;
- one selected real pilot evolution.

### Excluded

- autonomous edits to Hades core or its running source tree;
- automatic promotion;
- global or privileged package installation;
- operating-system configuration changes;
- autonomous credential acquisition or secret entry;
- irreversible database or state migrations;
- direct mutation of autobiographical memory;
- arbitrary third-party products embedded into the Hermes/Hades core tree;
- changing a live conversation's tools, system prompt, or generation;
- treating arbitrary logs, prompts, transcripts, or tool outputs as ledger
  content;
- general claims that Hades can evolve into any possible capability.

## Architecture

The system consists of four bounded subsystems plus a narrow runtime resolver.

### 1. Evolution Observer

The Observer consumes structured experience events, current organism evidence,
known workarounds, and prior suggestions. It emits `EvolutionSuggestion`
records and nothing else.

Responsibilities:

- aggregate failures by generation, capability, component, operation, and
  bounded signature;
- distinguish recurrence from one-off errors;
- associate recoveries and existing workarounds;
- rank candidate improvements by task impact, recurrence, confidence, reuse,
  risk, and expected cost;
- deduplicate semantically equivalent suggestions;
- suppress proposals already addressed by an active or pending generation;
- periodically surface a concise queue summary;
- optionally notify immediately about critical degradation without starting an
  evolution.

The Observer is deterministic for a fixed event set and scoring policy. An LLM
may explain a suggestion conversationally, but the queue identity, evidence,
deduplication, and ranking cannot depend on unrecorded model judgment.

It never injects a synthetic user or system message into a conversation.
Notifications use the existing activity/notification surface so message-role
alternation and prompt caching remain intact.

### 2. Autopoiesis Workshop

The Workshop starts when the user:

- invokes `/autopoiesis` with a free-form goal;
- invokes it with a suggestion ID; or
- accepts an Observer proposal.

The Workshop reads the relevant `gnothi_seauton` revision and evidence, asks
the user focused questions, compares approaches, and produces an immutable
`EvolutionBlueprint`. It may use the web only while a matching research
authorization is valid.

The Blueprint answers "what will Hades become?" and records:

- source suggestion or user goal;
- evidenced capability gap;
- desired observable outcome;
- alternatives considered and why one was selected;
- components to add, change, or remove;
- extension class for each component;
- expected sources, authors, licenses, and dependency families;
- required services and credential reference names, never secret values;
- affected capabilities, contracts, and organism areas;
- build and isolation strategy;
- test fixtures and the real pilot task;
- canary policy and allowed side effects;
- resource ceilings;
- expected organism diff;
- rollback triggers;
- known risks, unknowns, and operational cost.

The canonical Blueprint is serialized deterministically. Its digest is the
object authorized by build approval. Editing any material field creates a new
Blueprint revision and invalidates the former build authorization.

### 3. Evolution Builder and Generation Store

The Builder accepts exactly one approved Blueprint digest. It creates a
temporary candidate workspace, resolves only approved sources and dependencies,
runs class-specific validation, and materializes an immutable generation.

The Builder cannot:

- write into the stable installation or active generation;
- change active or last-known-good pointers;
- approve its own deviations;
- add an unlisted component, source, dependency family, credential, or side
  effect;
- use elevated privileges or global installers;
- modify authoritative memory or production configuration.

Any material deviation ends the build as `build_failed` or returns the
Blueprint to the Workshop for a new revision and approval. A dependency version
resolved within an explicitly authorized constraint is not a deviation if its
exact version and digest are captured before materialization.

### 4. Canary Supervisor

The supervisor runs outside the candidate process and owns:

- candidate startup and termination;
- side-effect-denied adapters;
- heartbeat and resource monitoring;
- stable-versus-candidate test orchestration;
- promotion report production;
- active and last-known-good pointer updates;
- post-promotion observation;
- automatic hard-failure rollback.

The candidate may emit health evidence but cannot write lifecycle state or
declare itself promotable. The supervisor verifies evidence independently.

The supervisor is an edge lifecycle service invoked by CLI or existing service
orchestration. It is not a new universally advertised model tool.

### 5. Runtime Generation Resolver

The resolver is the only narrow integration required by runtime loaders. At
new-session construction it:

1. reads a coherent active pointer;
2. validates the generation and manifest digests;
3. resolves the stable base plus the active overlay;
4. returns a frozen generation ID and resolved component locations;
5. records that generation ID on the session.

Skill, plugin, script, and MCP discovery consume the frozen resolution. They do
not reread `active.json` during the session. The system prompt and tool schemas
therefore remain stable for that conversation.

The resolver is generic overlay infrastructure. It contains no
autopoiesis-specific tool and does not let plugins modify core files.

## Data Flow

### Observation path

```text
runtime/component result
  -> bounded ExperienceEvent
  -> local event store
  -> deterministic aggregation
  -> deduplication against suggestions and generations
  -> ranked EvolutionSuggestion
  -> activity notification or explicit queue query
```

### Evolution path

```text
accepted suggestion or user goal
  -> research authorization
  -> workshop research and discussion
  -> immutable Blueprint digest
  -> build authorization
  -> isolated workspace
  -> quarantined artifacts
  -> immutable generation
  -> candidate organism revision
  -> external canary
  -> promotion report digest
  -> promotion authorization
  -> atomic pointer switch
  -> observation window
  -> stable or rolled_back
```

No stage silently grants authority to the next stage.

## Persistent Model

Autopoiesis state is profile-scoped beneath the resolved `$HERMES_HOME`:

```text
$HERMES_HOME/evolution/
├── evolution.db
├── generations/
│   └── <generation-id>/
│       ├── manifest.json
│       ├── skills/
│       ├── plugins/
│       ├── scripts/
│       ├── mcp/
│       ├── environments/     # lockfiles and reproducible environment specs
│       └── evidence/
├── workspaces/
│   └── <candidate-id>/
├── runtime/                  # rebuildable installed environments and caches
├── reports/
│   └── <report-digest>.json
├── active.json
└── last-known-good.json
```

### Evolution ledger

`evolution.db` is the authoritative append-only lifecycle ledger. Relational
tables store:

- suggestions and their evidence references;
- suggestion aggregation and supersession relationships;
- Blueprint revisions and canonical digests;
- authorization grants, scopes, expiry, consumption, and denial;
- candidate workspaces and build outcomes;
- generations and component identities;
- canary runs and bounded evidence references;
- promotion reports and approvals;
- activation, stabilization, retirement, and rollback events;
- supervisor health and recovery events.

Updates append events and derive current state transactionally. Existing
lifecycle events are never overwritten. Corrective records supersede prior
records while retaining their history.

The ledger must not contain:

- prompt or transcript bodies;
- credentials or secret values;
- arbitrary command or tool output;
- full unbounded stack traces;
- unnecessary local absolute paths;
- downloaded artifact bodies.

Detailed diagnostics remain protected evidence files with bounded retention and
ledger references.

### Baseline generation

First-time initialization creates an immutable empty-overlay baseline manifest
bound to the current stable Hades installation identity. Both pointers initially
refer to that baseline. This gives first promotion and rollback the same
semantics as all later transitions.

The stable base is never copied into the generation directory. Its release,
repository commit when available, compatibility version, and relevant
configuration fingerprint are recorded as provenance.

### Generation identity

Materialization follows this order:

1. stage candidate components in a private workspace;
2. compute every component and lockfile digest;
3. construct the canonical identity manifest without `generation_id` or
   mutable attestations;
4. calculate `SHA-256(canonical_identity_manifest)`;
5. use that digest as `generation_id`;
6. write the final manifest and copy the exact staged artifacts into a new
   generation directory;
7. set immutable/read-only permissions where the platform supports them;
8. reopen and revalidate all digests before marking the generation
   `quarantined`.

Attempting to create an existing ID with different bytes is an integrity
failure. Equivalent manifests and component bytes converge on the same ID.

### Generation manifest

Each manifest contains:

- schema version and generation ID;
- parent active generation ID;
- source suggestion and Blueprint digest;
- stable base identity and required Hades compatibility range;
- component class, logical ID, relative path, and digest;
- dependency constraints, resolved versions, lockfiles, and digests;
- source URL or package coordinate, author, license, and provenance;
- credential reference names and service prerequisites;
- capabilities provided or changed;
- invariants and verification commands;
- canary side-effect policy and resource ceilings;
- expected organism diff;
- build environment identity;
- creation timestamp and builder version;
- rollback plan and incompatibility reasons, if any.

All generation paths are relative to the generation root. Secret values and
machine-specific absolute paths are forbidden.

### Pointer documents

`active.json` and `last-known-good.json` contain:

- schema version;
- profile identity;
- generation ID;
- manifest digest;
- monotonically increasing lifecycle sequence;
- activation or designation timestamp;
- supervisor-produced integrity digest.

The integrity digest is
`SHA-256("hades-evolution-pointer-v1\0" + canonical_pointer_payload +
ledger_event_digest)`, where the payload excludes the integrity field itself.
It detects corruption and out-of-band edits within the MVP's local trust
model; it is not represented as a cryptographic signature against an attacker
running as the same operating-system user. The ledger records the same
transition and digest. A pointer whose sequence, generation, manifest,
integrity digest, or ledger event is incoherent is rejected.

Pointer replacement uses a temporary file, flush, atomic rename, and directory
flush where supported. A lifecycle lock and compare-and-swap on the expected
sequence prevent concurrent promotion or rollback.

## Lifecycle State Machine

The state machine belongs to one evolution attempt, identified independently
from its source suggestion and any generation it may eventually produce. A
failed or expired attempt does not delete or close the underlying suggestion;
starting again creates a new attempt with new authorization records.

The normal path is:

```text
draft
  -> research_authorized
  -> blueprint_ready
  -> build_approved
  -> building
  -> quarantined
  -> canary_running
  -> promotion_ready
  -> active
  -> stable
```

Terminal or lateral outcomes are:

```text
rejected
research_expired
build_failed
canary_failed
rolled_back
retired
```

Rules:

- only the Workshop creates Blueprint revisions;
- only a valid research grant permits web research;
- only a matching build grant enters `building`;
- only the Builder may propose `quarantined` after digest validation;
- only the supervisor enters or exits `canary_running`;
- only verified canary evidence produces `promotion_ready`;
- only explicit user approval permits `active`;
- only the supervisor's healthy observation window permits `stable`;
- only the supervisor may execute automatic `rolled_back`;
- no failed attempt or retired generation can become active without a new
  Blueprint, canary report, and promotion approval.

Every transition appends the actor, input digests, authorization reference,
timestamp, prior state, next state, and bounded reason to the ledger.

## Authorization Model

All approvals are explicit, scoped, expiring, single-use records. Silence,
timeout, ambiguous natural language, or approval of an earlier revision counts
as denial.

### Research authorization

Bound to:

- one suggestion ID or normalized user goal;
- permitted source classes and optional domains;
- a maximum duration;
- read-only web operations.

It permits search and retrieval of pages or documents for analysis. It does not
permit acquiring executable or dependency artifacts into the candidate,
authentication, installation, execution, configuration changes, or credential
access.

### Build authorization

Bound to:

- one exact Blueprint digest;
- enumerated component classes;
- allowed source and dependency families;
- candidate workspace boundaries;
- isolation and side-effect policy;
- resource and time ceilings.

It permits candidate workspace writes, acquisition of enumerated artifacts into
quarantine, isolated environment creation, validation, tests, and immutable
generation materialization.

It does not permit stable writes, pointer changes, global installation,
privilege escalation, new credentials, production side effects, or unplanned
components. A material deviation consumes no additional authority; work stops
and a new Blueprint must be approved.

### Promotion authorization

Bound to:

- one generation ID;
- one canary promotion-report digest;
- the expected current active generation and lifecycle sequence;
- the exact pointer switch operation.

It authorizes one atomic promotion attempt. It does not authorize rebuilding,
substituting a generation, skipping a changed report, or promoting a future
revision.

### Rollback authority

Automatic rollback needs no new user consent because it restores an already
approved last-known-good generation. It is limited to deterministic hard
failure triggers, appends evidence to the ledger, and notifies the user.

Manual rollback is an explicit operator command. It may select only a prior
healthy generation compatible with the current stable base. Rolling forward
again requires a new promotion approval and current canary evidence.

## Component Adapters

All adapters implement the same conceptual contract:

```text
validate_blueprint(component_spec)
acquire(component_spec, quarantine_root)
inspect(quarantined_artifact)
resolve_dependencies(quarantined_artifact, isolated_environment)
verify(quarantined_artifact, isolated_environment)
materialize(quarantined_artifact, generation_root)
describe_capabilities(materialized_component)
```

Inputs and outputs are typed records with relative paths and evidence digests.
Adapters cannot update lifecycle pointers or the ledger directly; the Builder
records their bounded results.

### Skill adapter

- validates required skill structure and complete instructional content;
- checks referenced local resources remain inside the skill root;
- records declared prerequisites and executable helpers;
- reuses existing skill quarantine and security checks where applicable;
- tests discovery through the real skill scanner in an isolated Hades home.

### Script and extension-pack adapter

- requires an explicit entry point and declared interpreter/runtime;
- rejects implicit global dependencies;
- creates a locked isolated environment;
- enforces execution time, output, filesystem, process, and network bounds;
- verifies the real invocation path used by the generated skill or plugin.

### Local-plugin adapter

- requires a versioned plugin manifest and supported registration interface;
- verifies imports and registration in a subprocess;
- rejects writes outside the candidate sandbox during validation;
- tests discovery and unload/termination behavior;
- does not permit plugin files to modify core source.

### MCP-configuration adapter

- validates server identity, command or transport, schemas, and capability
  declarations;
- stores credential reference names only;
- verifies startup and tool discovery with side-effect-denied credentials or
  fixtures;
- records which tools would become visible in a new session;
- refuses unapproved remote endpoints, commands, or environment variables.

## Quarantine and Build Isolation

Every acquired artifact enters quarantine before execution. The Builder records
source, retrieval time, author, license, declared version, actual digest, and
the approval that permitted acquisition.

The isolated build environment has:

- a private candidate workspace;
- a temporary profile-scoped Hades home;
- no stable generation write access;
- no active-pointer write access;
- no production credentials;
- network denied after approved acquisition unless the Blueprint explicitly
  grants a bounded test endpoint;
- bounded CPU, memory, wall time, subprocess count, output, and disk use;
- deterministic locale and relevant environment settings;
- dependency lockfiles and exact resolved digests.

Static validation precedes imports or execution. Executable inspection occurs
in a subprocess. A failure preserves bounded evidence and the quarantined input
for operator inspection, subject to retention policy; it never partially
materializes a generation.

## Canary Design

The canary compares the stable generation and candidate against the same
inputs. It starts from controlled copies or fixtures and cannot mutate
authoritative state.

The test suite includes:

1. stable reproduction of the target limitation;
2. candidate execution of the target capability;
3. common invariants for both generations;
4. real discovery and invocation paths for changed component classes;
5. startup, heartbeat, clean shutdown, and crash behavior;
6. resource ceilings;
7. forbidden filesystem, network, credential, and service access attempts;
8. candidate-scoped `gnothi_seauton` build and semantic diff;
9. compatibility with the current stable base;
10. deterministic repetition sufficient to exclude a one-off success.

Skipped, unavailable, indeterminate, stale, or partial evidence is not a pass.
The Blueprint identifies mandatory tests. A mandatory indeterminate result
produces `canary_failed`.

### Promotion report

The supervisor produces a content-addressed report containing:

- Blueprint goals and generation identity;
- stable and candidate results for each test;
- repetitions and variance;
- passed, failed, skipped, and indeterminate checks;
- resource and policy observations;
- expected versus actual organism diff;
- regressions and unresolved unknowns;
- material deviations, which must be empty;
- supervisor and verifier versions;
- bounded evidence references;
- promotion recommendation and exact rollback target.

The report digest is included in promotion approval. Changing report content
invalidates that approval.

## Promotion, Observation, and Rollback

### Promotion sequence

Under the lifecycle lock, the supervisor:

1. rereads and verifies the promotion grant;
2. verifies the expected active generation and sequence;
3. revalidates generation, manifest, report, and ledger digests;
4. atomically designates the current active generation as last-known-good;
5. atomically switches active to the approved candidate;
6. rereads both pointers and validates their ledger correspondence;
7. appends the activation result;
8. enables the post-promotion observation window for new sessions.

Failure before the active switch leaves the old generation active. Failure
after the switch but before complete verification invokes recovery: if the new
pointer cannot be proven coherent, the supervisor restores the prior pointer.

### Session behavior

Sessions record their resolved generation ID at creation and never change it.
Promotion and rollback affect only newly created sessions.

If a critical security or integrity failure implicates an active candidate,
sessions pinned to it are marked unsafe. They are terminated or require a
user-visible restart; their tools are not silently removed or replaced in
place. Noncritical quality concerns do not mutate existing sessions.

### Automatic rollback triggers

Automatic rollback is limited to:

- generation or component digest mismatch;
- pointer/ledger integrity mismatch;
- failure to load the generation through the real resolver;
- repeated startup failure or crash loop;
- repeated heartbeat loss beyond the fixed policy threshold;
- observed violation of the approved side-effect policy;
- hard Hades compatibility mismatch;
- loss of the supervisor's safe recovery control path.

Quality regressions, subjective dissatisfaction, cost drift without a declared
hard ceiling, and ambiguous results do not trigger automatic rollback. They
block stabilization, generate a visible warning, and await human action.

Rollback atomically restores last-known-good as active, records the failed
generation and trigger evidence, invalidates its pending stabilization, and
notifies the user. The failed generation remains immutable and inspectable.

## Gnothi Seauton Integration

Every organism fact is scoped to one of:

- stable generation;
- candidate generation;
- historical generation.

Candidate evidence cannot verify stable capabilities. Promotion changes the
default scope for new revisions but does not rewrite history. Rollback creates
a new organism revision tied to the restored generation.

Required semantic views are:

- current stable anatomy and capability state;
- one candidate versus its parent;
- active versus last-known-good;
- historical generation timeline;
- suggestion evidence to changed component trace;
- promotion or rollback evidence to affected capability trace.

No cross-generation relationship may imply that an unpromoted component is
available to stable sessions.

## Operator and Conversational Interfaces

The model-facing interaction is `/autopoiesis`, implemented through the
existing skill/slash-command pattern rather than a new universal core tool.
It accepts an optional suggestion ID or free-form goal.

Deterministic operator operations live under a CLI namespace equivalent to:

```text
hades evolution status
hades evolution suggestions [--state <state>]
hades evolution show suggestion <id>
hades evolution show blueprint <digest>
hades evolution show generation <id>
hades evolution show report <digest>
hades evolution workshop [<suggestion-id>]
hades evolution approve-research <request-id>
hades evolution approve-build <request-id>
hades evolution approve-promotion <request-id>
hades evolution canary <generation-id>
hades evolution history
hades evolution rollback [<generation-id>]
```

Exact command aliases may follow the existing registry, but the semantic
operations and approval separation are fixed. Read-only commands emit stable
JSON with `--json` for automation. Mutating commands support dry-run when a
meaningful preview exists. Approval commands display the bound digest and scope
and require interactive confirmation; non-interactive use must provide an
exact `--confirm-digest` value matching the request.

Observer notifications contain only a short explanation and suggestion ID.
They do not contain raw private evidence and do not interrupt a running model
turn.

## Privacy and Security

- Experience failure signatures are bounded and exclude user content by
  default.
- Redaction occurs before events enter the ledger or `gnothi_seauton`.
- Research authorization never exposes local files to web sources.
- Source retrieval follows the approved allowlist and records redirects.
- Downloads are never executed before quarantine and static inspection.
- Manifest and ledger records refer to credential names, not values.
- Candidate processes receive no production credentials in the MVP.
- Candidate state cannot write stable state, lifecycle pointers, or the ledger.
- Candidate reports are untrusted until independently verified by the
  supervisor.
- Local absolute paths are converted to relative artifact references or
  redacted.
- Exported reports and generated wiki views use public/sanitized identifiers.
- Retention removes expired workspaces and detailed evidence only after their
  required digests and bounded lifecycle facts are safely retained.

The MVP security boundary protects against buggy or unexpectedly invasive
candidate components. It does not claim containment against an adversary with
the same operating-system identity and arbitrary host execution; stronger
host isolation is a later hardening layer.

The `$HERMES_HOME/evolution` root and `evolution.db` reject statically hostile
pre-existing paths: symlinks, wrong owners, wrong object types, and non-private
modes. Path identity is revalidated around SQLite connection establishment;
retained descriptors and descriptor correlation are additional
defense-in-depth when the platform exposes them. These checks do not claim to
defeat a malicious same-UID actor or arbitrary code already executing inside
the trusted Hades host process. Candidate code must therefore run without the
trusted host's filesystem authority; Projects D and E own that isolation, and
crossing this candidate boundary is a critical failure rather than a condition
the SQLite connection layer can repair.

## Failure and Recovery Semantics

- A collector or Observer failure cannot block normal Hades operation.
- A Workshop interruption leaves an immutable draft or no record, never a
  partial approval.
- An expired approval stops the affected stage without side effects beyond
  already recorded read-only research or quarantined build artifacts.
- Builder crashes leave only a disposable workspace and append a recoverable
  failure record on reconciliation.
- Generation publication uses stage-then-rename and is idempotent by digest.
- Canary crashes fail the run and cannot promote the candidate.
- Concurrent lifecycle mutations serialize under one profile-scoped lock.
- Startup reconciliation compares pointer files, ledger sequence, generation
  manifests, and reports before accepting current state.
- An incoherent active pointer falls back to a proven last-known-good pointer.
- If neither pointer can be proven, Hades starts with the stable base and
  disables evolution overlays while surfacing a critical diagnostic.
- Backup and checkpoint restoration never silently changes the evolution
  ledger; reconciliation records any restored historical state explicitly.

## Testing Strategy

Tests assert behavioral relationships rather than fixed counts or snapshot-only
values.

### Unit and contract tests

- deterministic event normalization and bounded signatures;
- suggestion scoring, deduplication, suppression, and recurrence thresholds;
- canonical Blueprint and generation hashing;
- approval scope, expiry, consumption, and digest binding;
- state-machine transition permissions;
- manifest path and secret rejection;
- material-deviation classification;
- atomic pointer document validation;
- each component-adapter contract;
- report classification of failed, skipped, and indeterminate tests.

### Integration tests

- real skill discovery from stable base plus overlay;
- real plugin import and registration in a subprocess;
- real isolated script environment with locked dependency resolution;
- real MCP configuration discovery with a fixture server;
- candidate generation publication and immutability checks;
- `gnothi_seauton` stable/candidate diff without scope leakage;
- session pinning across promotion and rollback;
- prompt/tool-schema stability for an existing session;
- ledger and pointer crash recovery at every promotion boundary;
- forbidden candidate writes and network attempts;
- reconciliation after workspace, Builder, canary, and supervisor failure.

### Real-path end-to-end pilot

The pilot selection algorithm chooses the highest-ranked Observer suggestion
that satisfies every condition:

1. it is evidenced by recurring local events rather than a fabricated gap;
2. the original limitation is reproducible in an isolated environment;
3. success can be evaluated deterministically;
4. implementation requires no new credential, privileged install, core patch,
   or production write;
5. a skill or extension pack can provide the capability;
6. the user accepts it as the MVP pilot.

Ties are resolved by higher recurrence, then higher task impact, then lower
risk, then stable suggestion ID. If no suggestion qualifies, the MVP is not
declared complete; the Observer continues collecting evidence or the user may
provide a real task whose repeated failures can be recorded and reproduced.

The E2E test executes the complete authorization and lifecycle chain. It then
injects a controlled hard failure, verifies automatic rollback, and starts a
fresh session to prove last-known-good restoration.

External services may be represented by explicit fixture servers, but mocks
cannot replace the real Hades discovery, loader, approval, ledger, pointer,
session, or rollback paths.

## MVP Acceptance Criteria

The MVP is complete only when all of the following are demonstrated:

1. The Observer identifies and deduplicates a real recurring capability gap.
2. Hades proposes the improvement without starting research or mutation.
3. Research occurs only after a scoped research approval.
4. The Workshop produces a digest-stable Blueprint describing what Hades will
   become.
5. Build starts only after approval of that exact Blueprint digest.
6. The Builder produces an immutable peripheral overlay without modifying core
   or stable state.
7. All four A3 component adapters pass their real-path integration contracts.
8. Stable and candidate generations execute the same mandatory canary suite.
9. The candidate improves the selected real task without violating invariants.
10. `gnothi_seauton` reports an isolated and accurate candidate diff.
11. Promotion occurs only after approval of the exact generation and report.
12. Existing sessions remain pinned and unchanged across promotion.
13. A new session discovers and uses the promoted capability.
14. A controlled hard failure causes supervisor-owned automatic rollback.
15. Sessions pinned to the unsafe generation are not silently mutated.
16. The next session resolves the prior last-known-good generation.
17. The ledger reconstructs every decision, authorization, artifact digest,
    test result, promotion, and rollback without storing forbidden content.
18. Reconciliation recovers safely from interruption at every pointer-update
    boundary.
19. No core patch, global install, new credential, or unauthorized external
    side effect occurs.
20. Prompt caching and strict message-role alternation remain intact.

## Delivery Decomposition

The architecture is one product capability but must be implemented as bounded
subprojects. Each receives its own granular execution plan and acceptance
tests. Dependencies run in this order:

### Project A — Evolution contracts and ledger

Defines schemas, canonical serialization, state machine, profile-scoped storage,
authorization records, lifecycle lock, reconciliation, baseline generation,
and read-only status/history interfaces.

This project has no candidate execution and cannot activate overlays.

### Project B — Observer and structured experience bridge

Defines bounded events, aggregation, scoring, deduplication, suggestion queue,
notification behavior, and `gnothi_seauton` evidence references.

Depends on Project A for persistence. It has no web or write authority.

### Project C — Workshop, research gate, and Blueprint

Implements `/autopoiesis`, suggestion acceptance, research authorization,
collaborative design, deterministic Blueprint serialization, deviation rules,
and build-approval requests.

Depends on Projects A and B plus existing approval and web-research surfaces.
It has no builder authority.

### Project D — Builder, quarantine, and A3 adapters

Implements isolated workspaces, acquisition, provenance, lockfiles, class
adapters, immutable generation materialization, and candidate organism builds.

Depends on Projects A and C. It cannot change lifecycle pointers.

### Project E — Canary supervisor and promotion lifecycle

Implements stable/candidate orchestration, policy-denied adapters, report
generation, promotion approval binding, pointer switching, session resolution,
observation, reconciliation, and rollback.

Depends on Projects A and D. The runtime resolver integration is kept minimal
and receives focused cache- and alternation-safety tests.

### Project F — Authentic MVP pilot and closure

Selects the qualifying suggestion by the defined algorithm, runs the complete
real-path evolution, injects the controlled failure, verifies rollback, audits
privacy and provenance, and records acceptance evidence.

Depends on Projects B through E. It may reveal defects in earlier projects but
does not widen the feature scope.

## Execution Model Routing

This section governs how the implementation plan should assign work. It is a
project-execution policy, not a runtime dependency of Autopoiesis and not a
model-selection mechanism shipped to users.

Two model profiles are used:

- **Balanced implementer:** `gpt-5.6-terra` with `medium` reasoning by default.
  It handles granular, bounded work whose interfaces and acceptance tests are
  already explicit.
- **Frontier architect/reviewer:** `gpt-5.6-sol` with `high` reasoning by
  default. It owns irreversible contracts, security boundaries, concurrency,
  recovery, and cross-system review.

`xhigh` reasoning is reserved for the Project E activation/rollback protocol
and the final cross-system audit. `max` and `ultra` are not planned for the MVP;
they may be authorized only after a concrete blocking failure shows that
`xhigh` is insufficient.

The target allocation is at least 70 percent of implementation tasks to Terra.
Sol is used at design gates and on the small set of changes where a subtle
mistake could corrupt lifecycle authority, expose secrets, break prompt
caching, or defeat rollback.

### Project allocation

| Project | Primary implementation | Sol-owned work | Independent gate |
|---|---|---|---|
| A — Contracts and ledger | Terra `medium` for repositories, migrations, deterministic serializers, CLI reads, and ordinary tests | Sol `high` implements and validates canonical identity, authorization/state invariants, append-only semantics, lifecycle locking, and reconciliation | Sol `high` reviews schema evolution, crash consistency, and recovery proofs |
| B — Observer | Terra `medium` implements event normalization, aggregation, ranking, deduplication, notifications, and tests | Sol `high` reviews privacy boundaries, determinism, evidence sufficiency, and proposal suppression | Terra `high` runs real-path integration; Sol `high` signs off only on failed or ambiguous invariants |
| C — Workshop and Blueprint | Terra `medium` implements command/skill UX, canonical serialization, approval requests, and fixtures | Sol `high` implements and validates consent boundaries, material-deviation rules, research scope, and Blueprint digest contracts | Sol `high` reviews every path that could broaden an authorization |
| D — Builder and A3 adapters | Terra `high` implements each bounded adapter, quarantine plumbing, lockfiles, subprocess fixtures, and integration tests | Sol `high` implements and validates the Builder boundary, acquisition policy, sandbox contract, and common adapter interface | Sol `high` performs security and isolation review after all four adapters pass |
| E — Supervisor and lifecycle | Terra `medium` may implement fixtures, report rendering, deterministic test matrices, and non-authoritative CLI views | Sol `xhigh` implements or directly supervises resolver pinning, compare-and-swap pointers, crash recovery, promotion, unsafe-session handling, and rollback | A fresh Sol `xhigh` review validates failure injection at every atomic boundary |
| F — Authentic pilot | Terra `high` prepares reproducible evidence, fixtures, runbooks, and acceptance records | Sol `high` owns pilot selection validation, end-to-end diagnosis, privacy audit, and final architectural judgment | Sol `xhigh` performs the final MVP acceptance audit |

### Task-level routing rules

Every task in the implementation plan must carry:

- exact model and reasoning level;
- bounded objective and non-goals;
- allowed files or modules;
- prerequisite commits and contracts;
- tests to run and expected evidence;
- escalation triggers;
- a token-conscious context pack containing only the relevant specification
  sections, code paths, and prior task outputs.

Sol-owned work must still be decomposed until a Terra `medium` model could
follow the task, run its tests, and explain its acceptance evidence. Sol is
selected because the consequences require stronger judgment, not because the
task is vague or oversized.

Terra remains the default even for important work when the task has a narrow
interface and executable acceptance test. A task starts with Sol instead when
it changes any of:

- canonical hashing or version identity;
- authorization scope or approval consumption;
- ledger authority or lifecycle transitions;
- pointer atomicity, locking, or crash recovery;
- candidate isolation, credentials, network, or side-effect policy;
- session generation pinning, system-prompt stability, or tool-schema
  stability;
- automatic rollback triggers or recovery control.

An active Terra task escalates to Sol only if:

1. the specification and current code imply conflicting contracts;
2. the same test symptom survives two evidence-based repair attempts;
3. the fix would cross an authorization, secret, network, or stable-state
   boundary not named in the task;
4. a concurrency or crash-recovery invariant cannot be demonstrated
   deterministically;
5. the necessary change expands beyond the task's declared modules or
   acceptance contract.

Escalation passes a compact evidence packet, not the complete conversation:
task contract, relevant diff, failing command and bounded output, hypotheses
already tested, and the invariant in doubt. Sol decides or repairs the critical
point; bounded follow-up implementation returns to Terra when safe.

Reviews use a fresh context and must not rely on the implementer's narrative.
They inspect the diff, contracts, and test evidence directly. Model choice is
recorded in planning metadata and commit/evidence notes for cost analysis, but
never enters generation identity, product telemetry, or user data.

## Implementation Constraints

- Extend existing approval, logging, skill, plugin, MCP, worktree, graph, and
  configuration mechanisms before adding new permanent surfaces.
- Prefer CLI commands plus skills and service-gated operations over core model
  tools.
- Put behavioral configuration in `config.yaml`, never new non-secret `.env`
  variables.
- Keep files and modules focused on one bounded responsibility.
- Preserve contributor work and unrelated local changes.
- Use real imports and temporary profile-scoped Hades homes in E2E tests.
- Treat generated wiki, Neo4j projections, runtime caches, and human-readable
  report renderings as rebuildable views. Canonical promotion reports,
  immutable manifests, and ledger events remain content-addressed evidence.
- Do not coordinate or modify the remote backend as part of this local MVP
  unless a later project explicitly designs and authorizes that integration.

## Deferred Work

- governed core-patch generations;
- full profile cloning;
- automatic promotion;
- live migration of active conversations;
- production credentials inside canaries;
- host-level adversarial sandboxing;
- remote multi-host generation distribution;
- shared-backend evolution orchestration;
- autonomous memory lesson publication;
- marketplace discovery or automatic third-party plugin installation;
- quality-based automatic rollback driven by model judgment.

These require separate designs and approvals. They must preserve the immutable
generation, evidence, consent, cache-stability, and external-rollback contracts
defined here.
