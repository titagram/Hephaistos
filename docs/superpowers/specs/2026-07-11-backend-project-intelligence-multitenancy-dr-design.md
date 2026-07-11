# Backend project intelligence, multi-tenancy, and disaster recovery design

**Date:** 2026-07-11  
**Status:** approved in conversation, awaiting written-spec review  
**Scope:** DevBoard/Hades backend, backend frontend, analyzer pipeline, Neo4j
projection, Platon task clarification, organization tenancy, and disaster
recovery.

## 1. Purpose

The backend currently stores useful project data, but exposes several machine
oriented representations directly to humans. Raw memory chunks dominate the
memory page, generated wiki content is difficult to read, and the graph UI is
hidden and disconnected from the current Hades graph pipeline.

This design turns memory, wiki, and graph into coordinated projections of the
same project knowledge while preserving their distinct purposes. It also
defines how Platon consumes that knowledge, how organizations isolate it in a
multi-tenant backend, and how the entire service is recoverable after data
loss.

## 2. Current-state findings

- Neo4j is configured and running.
- The analyzer uses Graphify when import and extraction succeed. Otherwise it
  silently falls back to internal AST/regex extraction.
- The fallback provides useful Python analysis but mainly declaration
  inventory for PHP, TypeScript, JavaScript, Go, Java, and Ruby.
- The backend frontend already contains a `GraphPage`, but Graph is hidden from
  primary navigation.
- The dashboard graph API reads legacy `snapshots -> artifacts` data.
- Current Hades graph uploads are stored as `hades.php_graph.v1` or
  `hades.code_graph.v1` in `hades_agent_artifacts`.
- For the inspected project, two Hades PHP graph artifacts existed while no
  legacy graph snapshots existed. The existing graph page would therefore
  render an empty state despite available graph data.
- Neo4j rebuild and dashboard graph code still primarily use the legacy
  snapshot model, while Hades traversal reads Hades graph artifacts directly.
- The inspected project had a current Hades graph artifact but no legacy
  repositories, local workspaces, Genesis imports, or graph snapshots. Its
  overview therefore reported lifecycle states from a pipeline it did not use.
- Backlog Triage is implemented, but the inspected project had no tasks or
  previous suggestion. The UI did not distinguish an empty backlog from a
  never-run, unconfigured, running, failed, or stale analysis.

## 3. Architectural principles

1. Versioned canonical graph artifacts are the source of truth.
2. Neo4j is a rebuildable query projection, not authoritative storage.
3. Graphify is a preferred replaceable extractor, not the owner of the graph
   schema.
4. Dashboard, Platon, and agents use the same semantic query services.
5. Raw ingestion data remains inspectable but is not the primary human view.
6. Human and machine presentations may differ, but must share canonical data,
   provenance, freshness, and identifiers.
7. Every tenant boundary is enforced server-side.
8. A backup is successful only after a restore has been verified.

## 4. Canonical graph pipeline

```text
Source checkout
    -> Extractor interface
       -> GraphifyExtractor
       -> PythonAstExtractor
       -> LightweightExtractor
    -> Canonical versioned graph artifact
    -> Projection worker
    -> Neo4j
    -> Shared graph query service
       -> Architecture view
       -> Symbol explorer
       -> Impact analysis
       -> Version diff
       -> Agent traversal
```

### 4.1 Canonical artifact identity

Every graph version records:

- organization, project, repository, and workspace binding;
- branch and commit;
- schema version;
- extractor name and version;
- extraction mode and supported languages;
- generation timestamp and freshness state;
- artifact ID, checksum, and projection state;
- file coverage, extraction errors, and unparsed files;
- quality tier: `full`, `partial`, or `inventory_only`.

Stable symbol identifiers should survive commits when semantic identity has
not changed. Source locations remain version-specific metadata and must not be
the sole stable identifier.

### 4.2 Extractor contract

All extractors normalize into a Hades-owned canonical schema. Graphify remains
the preferred extractor because the internal fallback cannot reliably provide
cross-file callers, callees, and impact paths for the target language set.

Graphify failure must never be silent. The artifact and UI expose the selected
fallback and its reason. Features that require relationships are disabled or
clearly marked unavailable for `inventory_only` graphs.

Graphify may be removed only after an alternative passes a comparative corpus
for:

- symbol precision;
- caller/callee precision;
- cross-file resolution;
- stable identity across commits;
- PHP, TypeScript, and Python coverage;
- incremental performance;
- percentage of files successfully analyzed.

### 4.3 Neo4j projection

Projection is idempotent and keyed by canonical artifact/version. A worker:

1. validates the artifact;
2. imports nodes and edges into an isolated project/version scope;
3. creates or validates indexes;
4. records counts and projection checksum;
5. atomically marks the projection current;
6. retains the prior current projection until the new one is verified.

Neo4j loss is recoverable by replaying canonical artifacts. The dashboard and
agents receive an explicit `projection_unavailable`, `projection_stale`, or
`projection_rebuilding` state instead of silently returning an empty graph.

## 5. Graph product experience

Graph becomes a visible project section when the user has access. An empty
state distinguishes no artifact, failed extraction, low-quality fallback,
pending projection, and failed projection, and provides the relevant recovery
action.

### 5.1 Architecture

The default view aggregates large graphs into repository, module, namespace,
layer, and service nodes. It supports:

- search and filters;
- progressive expansion;
- node and edge type legends;
- freshness, commit, extractor, and quality indicators;
- navigation from a group to contained symbols;
- bounded previews rather than rendering the entire graph.

### 5.2 Symbol Explorer

Users search for a class or function and inspect a bounded neighborhood. The
view supports callers, callees, imports, inheritance, routes, models, and
progressive expansion where the extractor provides them.

Impact analysis is an action within Symbol Explorer. It traverses bounded
dependencies and shows affected modules, routes, tests, tasks, and documented
areas with direction, depth, and confidence.

### 5.3 Changes

Users compare two canonical graph versions, normally commits. The view shows:

- added, removed, and changed nodes;
- added and removed relationships;
- moved or renamed symbols when identity resolution is confident;
- changes in architectural groups;
- impact candidates introduced by the diff;
- extractor/schema mismatches that make comparison partial.

Comparisons across incompatible schemas or inventory quality tiers must be
labelled partial rather than presented as complete.

## 6. Project overview and unified lifecycle

The project overview describes user outcomes instead of internal pipeline
names. It answers four questions:

```text
Sources     Where does current project data come from?
Knowledge   Are curated memory and wiki current and usable?
Code model  Are indexing and graph capabilities usable?
Work        Are tasks present and has the backlog been analyzed?
```

Detailed pipeline terminology remains under Engineering, Runs, and Artifacts.

### 6.1 Code indexing instead of Genesis

`Genesis` is removed from the primary overview and replaced by `Code indexing`
with states `not_configured`, `indexing`, `ready`, `partial`, `stale`, and
`failed`. Details show source mechanism, repository/workspace, commit,
extractor, coverage, and last update.

`Genesis Import` remains a technical term in Runs/Artifacts for a full legacy
plugin import, where it can be contrasted with Delta Sync.

### 6.2 Code graph instead of Graph import

`Graph import` becomes `Code graph` and is derived from the canonical graph
service rather than the legacy `artifacts` table alone. Its states are
`unavailable`, `available`, `projecting`, `ready`, `degraded`, `stale`, and
`failed`.

A valid Hades graph artifact must never be reported as `not started` merely
because the project has no legacy graph snapshot.

### 6.3 Connected sources instead of Local workspace link

`Local workspace link` becomes `Connected sources`. A source is connected when
the project has at least one authorized current Hades workspace binding or a
legacy plugin local workspace. The summary shows source type, workspace or
repository, agent/device, branch, commit, last sync, and freshness.

Kickstart accepts both onboarding paths and must not block a Hades project on
the absence of legacy `local_workspaces` rows.

### 6.4 Backlog analysis

`Backlog Triage` becomes `Backlog analysis`. Its explicit states are
`no_tasks`, `ready`, `running`, `suggestion_available`, `stale`,
`configuration_required`, and `failed`.

Backlog analysis is project-aware by contract. Before recommending priorities
or grouping work, it builds a bounded evidence pack from:

- current tasks, statuses, owners, priorities, risks, and dependencies;
- curated project memory and relevant prior decisions;
- human and structured wiki sections;
- current graph architecture, symbol relationships, and impact paths;
- source, indexing, graph, memory, and wiki freshness/quality;
- prior accepted or rejected backlog analyses when relevant.

It must not present a technically informed recommendation from task text alone
when project knowledge is available. Every recommendation cites its evidence
and explicitly reports missing, stale, or degraded sources.

The feature remains read-only by default. It may group work, identify risks and
dependencies, and recommend ordering, but cannot mutate Kanban tasks without a
separate authorized action and explicit human approval.

An analysis becomes stale when material task fields, relevant curated
decisions, linked wiki sections, graph version, or source commit changes.

## 7. Memory, wiki, and graph information architecture

### 7.1 Memory

The default memory page shows compact, curated project knowledge:

- decisions;
- conventions;
- constraints;
- verified facts;
- resolved problems;
- operating procedures.

Raw chunks move to a secondary `Sources / Raw ingestion` view with grouping by
file and batch, search, filters, provenance, review state, and links to derived
curated entries. Supported states include `raw`, `proposed`, `accepted`,
`rejected`, and `superseded`.

Raw chunks remain retrievable for audit and debugging, but are not treated as
authoritative memory or shown as the main human representation.

### 7.2 Wiki

The same wiki URL provides two presentations of one canonical page model:

- `Human`: concise narrative documentation and navigation;
- `Structured`: typed blocks, identifiers, provenance, freshness, and machine
  references.

Canonical page blocks include summary, concepts, components,
responsibilities, workflows, decisions, risks, references, and linked graph
nodes. The two views must not drift into separately generated wikis.

### 7.3 Cross-links

- Wiki sections may link to verified graph nodes and graph versions.
- Graph nodes may link to relevant wiki pages and curated memory.
- Curated memory may cite graph nodes, commits, and wiki sections.
- Semantic suggestions are labelled as inferred until confirmed.
- Raw chunks do not automatically become Neo4j nodes.

The intended user model is:

```text
Memory = what the project knows
Wiki   = how the project is explained
Graph  = how the project is built
```

## 8. Platon memory-first task clarification

Platon builds a bounded task dossier before asking questions:

```text
Raw task
    -> classify goal, objects, constraints, and ambiguities
    -> retrieve curated memory, wiki, graph, prior decisions/tasks, and state
    -> classify known, inferred, conflicting, missing, and irrelevant facts
    -> rank material gaps
    -> ask at most 1-3 high-impact questions per cycle
```

### 8.1 Retrieval contract

Each selected context item records source, version/freshness, trust state,
relevant excerpt or structured fact, and selection reason. Graph retrieval is
used only when the task identifies technical components or symbols.

### 8.2 Question policy

Platon asks only when the answer can materially change scope, architecture,
behavior, risk, or acceptance criteria. It does not ask questions already
answered by project context. Safe and reversible assumptions are stated and
used without interruption.

Conflicting sources generate a clarification with the conflict summarized.
Missing but low-impact details become explicit assumptions.

### 8.3 Evidence-backed proposals

Options and recommendations cite the project facts that support them. The UI
shows concise reasoning with expandable references rather than raw memory
dumps.

### 8.4 Evaluation

A curated corpus of realistic tasks is annotated for required questions,
redundant questions, irrelevant questions, retrievable facts, conflicts, and
acceptable assumptions. Release metrics include:

- useful-question precision;
- questions already answered by context;
- missed critical decisions;
- source relevance and freshness;
- clarification cycles required before an executable task exists.

## 9. Multi-organization tenancy

Users have global identities and may belong to multiple organizations. They
select an active organization, but the backend derives authorization from a
server-side membership.

```text
Global user
    -> organization membership
       -> owner | admin | project_manager | developer | reviewer | viewer
       -> projects
          -> repositories/workspaces
          -> agents/tokens/queues
          -> memory/wiki/graphs/tasks
```

### 9.1 Enforcement

- Every tenant-owned record has an unambiguous organization path.
- Frequently queried or security-sensitive tables may also carry a direct
  organization ID for enforcement, audit, and future partitioning.
- Project UUID knowledge never grants access.
- Hades tokens are scoped to organization, project, and agent.
- All list, read, mutation, queue, search, and traversal paths verify effective
  membership and role.
- Background jobs persist tenant scope and revalidate authorization/policy at
  execution where appropriate.
- Cache keys, object paths, Neo4j labels/properties, logs, metrics, exports, and
  idempotency keys include tenant scope.

### 9.2 Lifecycle

The first release supports organization creation, invitations, membership
suspension/removal, role changes, ownership transfer, and users belonging to
multiple organizations. It also records immutable audit events for membership,
role, token, export, and destructive operations.

Selective per-organization restore is explicitly outside the first backup
release. Tenant deletion and export require separate lifecycle specifications.

## 10. Disaster recovery

The first release provides whole-backend disaster recovery, not selective
tenant restore.

### 10.1 Objectives

- PostgreSQL RPO: minutes through continuous WAL archiving.
- Object/artifact RPO: no more than one hour through incremental replication.
- Whole-service RTO target: four hours.
- Initial retention: 30 rolling days, with later policy extension possible.

### 10.2 PostgreSQL

- Periodic physical base backup.
- Continuous encrypted WAL archive for point-in-time recovery.
- Daily logical dump as a secondary recovery and inspection mechanism.
- Backup credentials have minimum required privileges.
- Encryption keys are stored separately from the application host and backup
  payload.

### 10.3 Object and artifact storage

- Versioning enabled.
- Encrypted incremental replication at least hourly.
- Off-site or separate-account copy.
- Signed manifest containing checksums, sizes, object versions, and database
  recovery marker.
- Restore tooling verifies that database references resolve to the matching
  object generation.

### 10.4 Neo4j

Canonical graph artifacts make Neo4j rebuildable. Daily Neo4j snapshots may
reduce RTO but are not correctness-critical. Recovery reconciles or rebuilds
projections and verifies counts/checksums before the graph service becomes
ready.

### 10.5 Verification and operations

- Backup jobs emit structured success/failure and freshness metrics.
- Missing or incomplete backups alert operators.
- Automated restores run in an isolated environment.
- Restore verification covers migrations, row counts, object checksums,
  required relationships, authentication isolation, and graph projection.
- A complete disaster-recovery exercise is performed at least monthly.
- Production restore requires explicit human authorization and is audited.

## 11. Failure behavior

- Extractor failure records an explicit degraded artifact or failed run.
- Projection failure does not replace the last verified current projection.
- Missing Neo4j does not delete or invalidate canonical artifacts.
- Missing raw chunks do not make curated memory appear verified.
- Stale wiki, memory, or graph evidence is labelled and down-ranked by Platon.
- Cross-tenant mismatch fails closed and creates a security audit event.
- Backup freshness outside the objective raises an operational alert.
- A restore that has not passed verification remains non-ready.

## 12. Testing strategy

### 12.1 Graph and Graphify

- Contract tests for each extractor and canonical schema.
- Comparative corpus tests across PHP, TypeScript, and Python.
- Stable-ID and version-diff tests across representative commits.
- Projection replay, idempotency, partial failure, and checksum tests.
- Architecture aggregation, bounded traversal, impact, and diff API tests.
- Frontend tests for empty, degraded, stale, rebuilding, partial, and current
  graph states.

### 12.2 Project overview, memory, wiki, and assistants

- Unified overview-state tests for Hades-only, plugin-only, mixed, empty,
  stale, degraded, and failed projects.
- Tests proving that Hades artifacts and workspace bindings satisfy canonical
  graph/source states without legacy snapshot/workspace rows.
- Backlog analysis state, freshness, and invalidation tests.
- Evidence-pack tests proving that task, memory, wiki, graph, decisions, and
  freshness inputs are used or explicitly reported unavailable.
- Tests proving backlog analysis remains read-only without a separately
  authorized and human-approved mutation.
- Human/structured wiki rendering from the same canonical page.
- Raw-to-curated provenance and state transition tests.
- Cross-link authorization and version tests.
- Platon retrieval, conflict, question-ranking, and evaluation-corpus tests.

### 12.3 Tenancy

- Authorization matrix tests for every role and resource class.
- Cross-tenant negative tests for IDs, search, queues, caches, exports, graph,
  memory, and wiki.
- Membership lifecycle and ownership-transfer tests.
- End-to-end tests with one user in multiple organizations.

### 12.4 Disaster recovery

- Automated base backup plus WAL restore to a chosen timestamp.
- Hourly object delta restore with manifest verification.
- Full isolated restore and Neo4j rebuild.
- Corrupted, incomplete, stale, and mismatched backup tests.
- Measured RPO/RTO evidence retained with each recovery exercise.

## 13. Delivery decomposition and priority

This design must not be implemented as one change. Recommended order:

1. **Graph foundation:** canonical artifact contract, extractor observability,
   unified graph read service, and projection state.
2. **Project overview and graph product:** unified Hades/plugin lifecycle,
   meaningful status cards, visible navigation, Architecture, Symbol Explorer,
   impact, and version diff.
3. **Memory/wiki presentation:** curated/default memory, raw-source view, dual
   wiki rendering, and cross-links.
4. **Project-aware assistants:** evidence-backed Backlog analysis followed by
   Platon's bounded memory-first dossier, gap analysis, citations, and a shared
   evaluation corpus.
5. **Disaster recovery baseline:** WAL/base backups, hourly object replication,
   isolated restore verification, and runbook. This may run in parallel with
   product UI work once storage inventory is complete.
6. **Multi-tenancy foundation:** organization schema, memberships,
   authorization matrix, tenant-scoped tokens and jobs, followed by migration
   of existing data into a default organization.

Multi-tenancy changes the security boundary and requires its own detailed
specification and migration plan before implementation. Disaster recovery must
be operational before migrating valuable production tenants.

## 14. Explicit non-goals

- Neo4j as authoritative storage.
- Rendering the entire graph by default.
- Automatic conversion of raw memory chunks into graph nodes.
- Separately generated human and machine wikis.
- Silent Graphify fallback.
- Selective per-organization restore in the first backup release.
- Combining all delivery tranches into one implementation branch.
