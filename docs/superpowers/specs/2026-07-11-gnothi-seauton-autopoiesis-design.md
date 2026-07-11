# Gnothi Seauton and Autopoiesis Design

**Date:** 2026-07-11
**Status:** Approved design
**First implementation scope:** gnothi_seauton
**Umbrella project:** autopoiesis

## Summary

Hades should understand its complete installed organism, notice repeated
capability failures, propose how it could evolve, build a candidate generation
in isolation, prove it with a canary, and promote it only after explicit user
approval.

The project has four ordered subsystems:

1. gnothi_seauton: an evidence-backed model of the complete organism;
2. Evolution Observer: structured failure analysis and a suggestion queue;
3. /autopoiesis: collaborative research and Evolution Blueprint creation;
4. self-versioning: candidate generations, canary, promotion, and rollback.

Only gnothi_seauton is in the first implementation scope. The complete roadmap
is retained here so the foundation does not preclude later phases.

## Product Intent

Hades already creates and improves skills, stores durable memory, installs
external skills through quarantine and scanning, discovers plugins, and can
gate writes behind user approval. These mechanisms do not yet form a governed
capability-acquisition loop.

The intended loop is:

    repeated capability failure
      -> evidence-backed self-inspection
      -> queued evolution suggestion
      -> scoped permission to research
      -> collaborative Evolution Blueprint
      -> user approval to build
      -> isolated candidate generation
      -> canary verification
      -> user approval to promote
      -> atomic promotion or automatic rollback
      -> verified capability and durable learning

The system extends Hades at the narrowest viable layer. It may eventually
create or acquire skills, scripts, plugins, MCP configuration, dependencies,
and core patches. Core modification is allowed only as a versioned candidate,
never as an in-place autonomous edit of the running generation.

## Naming

The Greek phrase gnōthi seauton means “know thyself.” The machine identifier is
gnothi_seauton; the CLI namespace is “hades gnothi-seauton.”

Autopoiesis is the project and slash-command name for Hades producing a
governed new version of its own organism.

## Foundational Decisions

1. Hades models its entire installed organism, not only core source.
2. The self model is derived from current evidence. Narrative memory cannot
   make a self-model claim true.
3. The generated wiki is a disposable view, not a source of truth.
4. gnothi_seauton is read-only.
5. Repeated failures may create a queued suggestion, but cannot automatically
   start research, construction, installation, or promotion.
6. Internet research permission is scoped to one accepted suggestion and does
   not authorize downloads, execution, or installation.
7. Blueprint approval authorizes construction only. Promotion requires a
   separate approval after canary evidence exists.
8. Candidate code belongs to a distinct immutable generation.
9. Rollback is owned by a supervisor outside the process being modified.
10. The conversation system prompt and toolset remain byte-stable. Self-model
    content is retrieved on demand.

## Relationship to Existing Hades Architecture

The Canonical Graph Foundation remains the generic source-aware graph contract
and projection mechanism. gnothi_seauton extends it with an organism ontology
and organism-oriented query views. It must not create a competing graph, wiki,
freshness model, or evidence store.

Canonical graph artifacts are authoritative records. Neo4j, search indexes,
and the generated wiki are rebuildable projections. Local operation uses the
same artifact contract in the profile-scoped Hades data directory when the
shared backend is unavailable.

Existing service-gated graph and awareness surfaces gain an organism scope.
No universally advertised core model tool is added.

## Gnothi Seauton Architecture

### Evidence planes

The organism graph combines three planes without conflating them.

**Definition plane.** Describes what the organism can be according to
versioned definitions:

- repositories, modules, symbols, entry points, and internal dependencies;
- tool registrations and toolsets;
- slash and CLI commands;
- skills and declared prerequisites;
- plugins, hooks, and provider manifests;
- MCP declarations and capabilities;
- packages, binaries, services, tests, and explicit invariants.

**Runtime plane.** Describes the active generation:

- generation, release, and commit identity;
- resolved configuration with secret values removed;
- loaded tools, plugins, skills, and MCP servers;
- processes and managed services;
- dependency availability;
- health-check and capability-probe results.

Declared and effective configuration remain distinct. Presence on disk does
not imply that a component is loaded or usable.

**Experience plane.** Describes observed behavior:

- bounded failure signatures;
- repeated task and tool failures;
- retries and recovery outcomes;
- health-check and canary results;
- promotions and rollbacks;
- capability verification evidence.

This plane stores structured facts and protected evidence references, not
complete prompts, conversations, or arbitrary tool output.

### Organism scope

The model covers core source and installed releases, active profiles and
effective configuration, all classes of skill and plugin, configured MCP
servers, package and binary dependencies, external services, local and shared
backend components, managed surfaces and workers, and stable, candidate, and
historical generations.

Every component records whether it is core, bundled, user-local, third-party,
or an external service.

### Collectors

Collectors are independent and read-only. They emit declarative artifacts and
never write directly to the graph.

- **Source Collector:** modules, symbols, entry points, source dependencies,
  commits, ownership, and test relationships.
- **Capability Collector:** tools, commands, skills, plugins, MCP servers,
  registrations, prerequisites, and provided capabilities.
- **Runtime Collector:** generation identity, effective non-secret
  configuration, loaded components, services, and probes.
- **Contract Collector:** machine-readable invariants derived from tests,
  versioned documentation, schemas, and explicit contracts.
- **Dependency Collector:** packages, binaries, services, constraints, and
  the components or capabilities requiring them.
- **Experience Collector:** normalized errors, retries, recoveries, health
  checks, canaries, promotions, and rollbacks.

Important contracts include prompt-cache stability, strict message-role
alternation, approval boundaries, profile isolation, and backward-compatible
artifact formats.

### Snapshot pipeline

Every full or incremental build:

1. acquires a coherent organism and generation identity;
2. runs relevant collectors;
3. redacts secrets and unnecessary local identifiers;
4. validates collector artifacts;
5. normalizes them into the canonical contract;
6. validates cross-references and ownership;
7. calculates freshness, coverage, and quality;
8. atomically publishes an immutable revision;
9. rebuilds projections and generated views;
10. retains the prior healthy revision.

A collector failure does not erase healthy knowledge. The revision is partial,
names failed domains, and may carry forward prior evidence with its original
verification time. Carried-forward facts are not freshly verified.

### Identity and provenance

Every entity and relationship has:

- a stable logical identifier;
- entity or relationship type;
- owning component and ownership class;
- stable, candidate, or historical generation scope;
- source artifact and evidence reference;
- source version or checksum;
- collection and verification timestamps;
- extraction quality and freshness state.

Raw credentials, cookies, private keys, bearer tokens, full local paths, and
unbounded exception messages are forbidden.

### Capability state

Capability dimensions are independent:

- declared: a definition claims it;
- installed: its provider is present;
- available: prerequisites are satisfied;
- active: its provider is loaded;
- verified: a current probe or real-path test proved it;
- degraded: evidence shows current or repeated impairment;
- candidate: it exists only in an unpromoted generation.

A plugin can be installed and active while a network capability is unavailable.
Verified always requires current evidence. Missing or stale evidence produces
unknown, never inferred success.

### Generated wiki

The wiki is fully generated from an organism revision and contains:

1. Anatomy;
2. Capabilities;
3. Dependencies;
4. Contracts and invariants;
5. Runtime state;
6. Known degradation;
7. Generations, promotions, and rollback history;
8. Coverage, freshness, and unknown areas.

Every section links to its graph entities and evidence. Manual edits are not
self-model truth. Human design intent can contribute only as versioned
documentation collected as evidence and regenerated into the view.

## Operational Interfaces

The local CLI exposes:

    hades gnothi-seauton status
    hades gnothi-seauton rebuild
    hades gnothi-seauton inspect <component>
    hades gnothi-seauton explain <capability>
    hades gnothi-seauton diff <revision-a> <revision-b>

Status reports coverage, freshness, revision identity, failed collectors, and
whether precise self-analysis is safe. Rebuild creates an immutable revision
without modifying the organism. Inspect returns anatomy, ownership, state,
dependencies, contracts, and bounded observations. Explain traces a capability
through providers and prerequisites to evidence. Diff reports semantic changes:
capabilities, dependencies, invariants, runtime state, and evidence quality.

The /gnothi_seauton slash command provides the conversational view. Existing
graph and awareness queries use organism scope. A rebuild never changes the
tool schema of an active conversation.

## Incremental Refresh and Drift

A full rebuild is always the recovery path. Incremental refresh is triggered by
a core release or commit change; plugin, skill, or MCP lifecycle event;
effective configuration change; dependency or service transition; process
generation change; or health, canary, promotion, and rollback event.

Each event invalidates only affected domains. A periodic drift check compares
collector fingerprints with the current revision. Full and incremental builds
must converge to the same semantic model for the same organism.

## Structured Experience Events

Text logs remain operator evidence. Hades additionally emits bounded events:

    event_type
    timestamp
    generation_id
    component_id
    capability_id
    operation
    failure_class
    bounded_signature
    severity
    retry_count
    task_impact
    recovered
    evidence_refs

Signatures support aggregation without raw exception text or user content.
Detailed diagnostics remain protected artifacts with separate retention.
Repeated events aggregate by generation, component, capability, and signature.

gnothi_seauton describes observations but never turns an error directly into
an evolution proposal. That belongs to Evolution Observer.

## Failure, Privacy, and Security

- Collector failures are isolated by domain.
- Invalid artifacts are rejected before publication.
- Publication is atomic and retains the last healthy revision.
- Projection failure does not destroy canonical artifacts.
- Schema incompatibility rebuilds from source evidence.
- Stale evidence stays visibly stale.
- Unknown coverage is explicit.
- Candidate evidence cannot verify stable capabilities.
- Collectors are read-only and least-privileged.
- Redaction happens before artifacts leave collector boundaries.
- Backend upload preserves project and workspace-binding authorization.
- No prompts, transcripts, arbitrary outputs, secrets, complete dumps,
  unbounded logs, or unnecessary absolute paths enter the model.

## Testing Strategy

Tests assert relationships and behavior, not fixed enumeration counts.

- **Collector contracts:** deterministic, valid, redacted artifacts and bounded
  degradation when optional infrastructure is missing.
- **Graph invariants:** every provider, prerequisite, ownership edge, evidence
  reference, and generation reference resolves.
- **Runtime truth:** real registration paths distinguish all capability
  dimensions.
- **Freshness and drift:** real component changes invalidate correct domains
  without invalidating unrelated ones.
- **Partial builds:** collector failure preserves prior evidence, original
  freshness, and explicit missing coverage.
- **Generation isolation:** candidate facts never enter stable queries before
  promotion.
- **Redaction:** sensitive fixtures never reach artifacts, projections, wiki,
  or structured events.
- **Rebuild equivalence:** full and incremental construction converge.
- **Real-path E2E:** a temporary checkout and isolated HERMES_HOME exercise real
  imports, discovery, configuration, artifact publication, and query.

Mocks may isolate unavailable external services but cannot replace the
resolution chain under test.

## Gnothi Seauton Acceptance Criteria

The first subsystem is complete when:

1. Hades inventories the full installed organism.
2. It distinguishes all capability-state dimensions.
3. It explains a capability through providers and prerequisites to evidence.
4. It detects drift after a real organism change.
5. It compares two organism revisions semantically.
6. It generates a fully derived, evidence-linked wiki.
7. It exposes stale, partial, and unknown coverage.
8. It leaks no secrets and performs no mutation.
9. It preserves prompt and tool-schema stability.
10. It supplies enough impact evidence for a future Evolution Blueprint.

## Autopoiesis Roadmap

The following phases are approved umbrella scope but require separate designs
and implementation plans after gnothi_seauton.

### Phase 2: Evolution Observer

The observer consumes structured experience events and current self-model
evidence. It creates deduplicated Evolution Suggestions ranked by task impact,
recurrence, diagnosis confidence, reuse, risk, and cost.

Suggestions are queued and summarized periodically. They do not interrupt
normal work. Critical degradation may notify immediately, but cannot start an
evolution.

Each suggestion records the evidenced capability gap, affected tasks and
components, frequency, recovery history, confidence, candidate evolution
class, and current workaround.

### Phase 3: Autopoiesis Workshop

The workshop starts when the user invokes /autopoiesis, accepts a suggestion,
or accepts Hades’s invitation after repeated failures.

It uses gnothi_seauton to understand the organism and brainstorms with the user.
Before internet research, Hades requests permission scoped to one suggestion,
source classes, and duration. Approval permits read-only research only. It does
not permit download, install, execution, authentication, or configuration
changes.

### Phase 4: Evolution Blueprint

The workshop answers “what will Hades become?” with:

- evidenced gap and desired outcome;
- alternatives considered;
- components created, acquired, modified, or removed;
- sources and licenses;
- permissions, credentials, and services;
- affected capabilities and invariants;
- migrations;
- build and isolation strategy;
- verification and canary plan;
- rollback triggers;
- cost, risk, and operational impact.

The first approval gate authorizes construction only. Material deviations must
be reported before promotion.

### Phase 5: Candidate Generation

The builder creates an immutable candidate in an isolated checkout and runtime.
It may contain skills, scripts, plugins, MCP configuration, dependency changes,
and core patches.

Acquired artifacts pass quarantine, provenance and license capture, security
scanning, and class-appropriate approvals. Candidate facts and tests remain in
candidate scope.

### Phase 6: Canary

An external supervisor launches the candidate separately with synthetic tasks,
fixtures, copied state, least-privilege credentials, and side-effect-denied
adapters. It cannot send real messages, mutate authoritative memory, change
production configuration, or perform unapproved external writes.

The canary evaluates startup, heartbeat, migration compatibility, invariants,
regressions, target capability, resources, errors, and semantic organism diff.

### Phase 7: Promotion Gate

Hades presents Blueprint goals versus results, test and health evidence,
deviations, organism diff, remaining risks, unknowns, and the rollback point.

A second explicit approval authorizes promotion. Silence, timeout, or Blueprint
approval never counts as promotion approval.

### Phase 8: Promotion and Rollback

Promotion atomically switches the active-generation pointer. The external
supervisor owns the pointer and last-known-good generation.

Automatic rollback triggers include startup failure, missing heartbeat,
critical invariant violation, incompatible migration, sustained critical
errors, or loss of the safe recovery control channel.

Rollback restores the last known-good generation and compatible state. Failed
candidates remain inspectable but inactive.

Only after promotion and a healthy observation window do candidate
capabilities become stable and verified. Procedural use can then update skills,
durable lessons can be proposed to memory, provenance remains in
gnothi_seauton, and the previous generation remains available for rollback.

## Delivery Sequence

1. Complete or reuse Canonical Graph Foundation prerequisites.
2. Implement organism contracts and collectors.
3. Implement local/backend publication and read-only queries.
4. Implement generated wiki, drift, structured events, and E2E acceptance.
5. Specify and implement Evolution Observer separately.
6. Specify and implement Workshop and Blueprint separately.
7. Specify and implement supervisor, canary, promotion, and rollback separately.

Later designs may refine internals but must preserve evidence requirements,
approval boundaries, prompt-cache invariants, candidate isolation, and external
rollback ownership.

## Initial Non-Goals

The first gnothi_seauton implementation does not propose evolutions, browse,
download or install components, edit the organism, start candidates, run
canaries, promote versions, replace existing memory/wiki/graph/log systems,
inject the self model into the prompt, or claim complete knowledge from stale
or partial evidence.
