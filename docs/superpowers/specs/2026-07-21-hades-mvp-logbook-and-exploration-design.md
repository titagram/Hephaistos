# Hades usable-MVP: project logbook and graph exploration design

**Status:** approved design input for the Graph Lifecycle v2 execution plan

## 1. Outcome and boundary

The first usable Hades MVP must let a project member answer four practical
questions without needing to understand graph schemas, Neo4j, scopes, or graph
versions:

1. What happens when this endpoint or entry point runs?
2. What is connected to this route, symbol, file, or class?
3. What are the important parts of this application and how do they relate?
4. What changed in this project, who made the change, and what evidence supports it?

The canonical Graph Lifecycle v2 design remains normative for graph data,
verification, authorization, and uncertainty. This document narrows the MVP
experience around it. It does not add a second graph protocol, a v1 adapter, a
new Hades core model tool, or a replacement for project Wiki pages.

The first MVP target is a disposable Symfony Demo project and a normal local
Hades Agent installation. Carnovali is a later scale gate only. Production
cutover, v1 retirement, and full disaster-recovery promotion stay outside this
MVP acceptance target; the existing backup/cutover plans remain required before
any destructive or production operation.

## 2. Chosen architecture

Three plausible logbook approaches were considered:

| Approach | Benefit | Rejected cost |
|---|---|---|
| Generated audit feed only | little UI work | cannot capture an agent's useful explanation, intent, or residual uncertainty |
| Markdown Wiki/log file | familiar to humans | weak project isolation, querying, actor attribution, idempotency, and cross-linking |
| **Append-only project event ledger with optional Markdown narrative** | precise machine record and readable human history | requires one small backend resource and a CLI/skill integration |

The MVP uses the third approach. Backend-originated events and Hades-authored
entries write to one project-scoped append-only ledger. The ledger is canonical
for operational history; the Wiki remains canonical for maintained project
knowledge. A logbook entry may link to a Wiki revision, graph import, graph
projection, verification work item, Kanban item, commit, or file reference, but
does not duplicate or mutate those resources.

## 3. Project logbook contract

### 3.1 Entry shape

The backend stores `ProjectLogbookEntry` records. The implementation may choose
the final migration/table naming convention, but every record has exactly these
logical fields:

| Field | Contract |
|---|---|
| `id` | backend-generated ULID |
| `project_id` | mandatory project boundary; never inferred from a repository label |
| `occurred_at`, `recorded_at` | UTC event time supplied/validated by server and UTC insertion time |
| `actor` | immutable structured identity: `kind` (`user`, `agent`, `subagent`, `system`), stable ID where available, display label, optional Hades instance ID, role, and model |
| `event_type` | one of `change`, `creation`, `import`, `projection`, `verification`, `wiki`, `decision`, `failure`, `rollback`, `note` |
| `severity` | `info`, `warning`, or `error` |
| `summary` | required, plain-language, at most 240 Unicode code points |
| `narrative_markdown` | optional concise human explanation; rendered read-only and sanitized |
| `references` | typed, bounded list of project-local resource references plus optional commit/file identifiers; no opaque unvalidated URLs |
| `correlation_id` | optional stable operation/run identifier for grouping related events |
| `idempotency_key` | mandatory for external writes; unique within the project |
| `payload` | bounded server-validated structured facts; technical and collapsed by default |
| `supersedes_entry_id` | optional same-project earlier entry; records a correction without rewriting history |

Entries are immutable. There is no update or delete endpoint. A correction,
retraction, or later result is a new entry that references the preceding one.
The backend rejects a reference to another project, secret-like text, token-like
values, unsafe absolute paths, oversized narrative/payload content, and an
idempotency key already used for a non-identical request.

### 3.2 Writers and authorization

The backend centralizes all writes in `ProjectLogbookService`; controllers,
import/projection services, verification services, and agent endpoints may not
write the persistence model directly.

- A project member may read entries visible to that project.
- A project administrator may create a human-authored `note` or `decision`.
- The backend creates trusted system events for graph import/projection,
  verification transitions, Wiki publication, and rollback/failure transitions.
- A registered Hades Agent may append only to its own project and only when its
  registered capability set contains `write_project_logbook`. The server derives
  the agent/device identity from authentication; the agent may not impersonate a
  user, a different agent, or the system.
- An agent or subagent must include a correlation/idempotency key and may add a
  narrative only about an action it actually completed, failed, or intentionally
  stopped. Pure reads, searches, and speculative plans do not create entries.
- Agent actions that modify the project create a logbook entry after the
  durable result is known. Failed or rolled-back actions create an `error` or
  `rollback` entry with the failure class and recovery state, never a false
  success entry.

The server is the clock of record. It validates project authorization before
deduplication, so a matching key from another project cannot disclose anything.

### 3.3 API and agent surface

The backend provides these project-scoped operations:

```text
GET  /api/v1/projects/{project}/logbook?cursor=&types=&actor=&from=&to=&q=
GET  /api/v1/projects/{project}/logbook/{entry}
POST /api/v1/projects/{project}/logbook
```

The list response is cursor-paginated in reverse chronological order and returns
only plain DTOs with references already authorization-checked. Query text is a
bounded server-side search over summary and narrative; it does not expose raw
technical payloads as a search index.

Hades exposes the capability as a CLI command and a dedicated skill, not as a
new core model tool:

```text
hades backend logbook list [project/binding filters]
hades backend logbook show <entry-id>
hades backend logbook write --type ... --summary ... --narrative-file ... \
  --reference ... --idempotency-key ...
```

The skill instructs an agent to write one factual, concise entry after a
successful mutation or meaningful terminal failure. It obtains IDs, commit SHAs,
test results, and reference handles from the completed workflow; it never asks a
model to invent them. If the backend is temporarily unavailable, the existing
Hades backend outbox retains a typed pending entry and retries it idempotently
on the next authenticated sync. A permanent authorization rejection remains
visible locally and is not retried indefinitely.

## 4. Human-facing logbook

The backend project navigation includes **Logbook**. The default view is a
human-readable chronological timeline: actor, action summary, time, status, and
linked resources. The details drawer shows the Markdown narrative and a
collapsed technical evidence section. Filters cover event type, actor,
severity, time range, and free-text search. Correlated entries can be grouped
or expanded individually. Wiki, graph, import, verification, and Kanban links
open the existing project-scoped page in a new route/state; a missing or no
longer-authorized reference is shown honestly instead of becoming a broken link.

The logbook is not a second Kanban and not a general chat. It records durable
facts and short explanations. Long-lived decisions belong in the Wiki and link
back to the decision event.

## 5. Graph explorer MVP

The Graph Lifecycle v2 explorer already defines the implementation-level
contract. For MVP the primary UI has exactly these modes:

| Mode | Normal user intent | Availability |
|---|---|---|
| Follow an entry point | follow a web route, CLI command, job, `main`, or other discovered entry point from start to terminal outcomes | default when entry points exist |
| Analyze an element | find a route, symbol, class, function, or file and inspect bounded callers, callees, dependencies, and impact | always available |
| Explore architecture | progressively reveal categorized bounded graph areas and filter them; never render the full graph at once | available when architecture categories exist |
| Compare connections | compare two selected elements and show a bounded path/no-path/unknown outcome | advanced disclosure only |

The initial screen uses one clear purpose sentence, a plain-language mode
choice, and a dynamic selector. It never leads with graph counters, ULIDs,
scope IDs, projection versions, raw quality flags, or a non-functional button.
Technical metadata is available in a collapsed disclosure. Empty, partial,
unknown, stale, and failed states have distinct copy and behavior; an incomplete
graph never displays a false zero.

For applications with callable routes, **Follow an entry point** leads with
HTTP endpoints. For CLI, workers, compiled applications, or mixed systems, it
uses discovered generic entry points. The graph provides static paths and their
branch/exception/async provenance; it must include every statically reachable
branch that the parser can prove, not only a guessed happy path. Dynamic facts
that cannot be proven become explicit verification work, not inferred edges.

The rendered lifecycle is bounded and progressively expandable. The browser
never receives the entire project graph merely to create an overview. All
interactions map to a closed backend operation and have tested success, empty,
unknown, stale, authorization, and retry behavior.

## 6. MVP acceptance slice

The MVP is complete only when a fresh small Symfony Demo project can be indexed
from a local Hades Agent and a human can:

1. see import/projection progress and its matching logbook events;
2. open Graph, choose an HTTP route, and follow a lifecycle with visible
   branches and terminal outcomes;
3. analyze a route/class/function and receive bounded relationships with an
   honest completeness state;
4. change to architecture exploration without loading every node;
5. open advanced comparison and distinguish path, no-path, and unknown;
6. see a verification item for intentionally unresolved/dynamic evidence and
   process it through the agent's verification workflow;
7. open the project Logbook, identify the human/agent/system actor and evidence
   for each tested change, and navigate to linked project resources; and
8. resume a temporarily interrupted agent sync without duplicate logbook
   entries or lost terminal result.

The acceptance run records a concise final logbook entry with the fixture SHA,
agent version/instance, graph import/projection handles, tested route, commands
and test evidence, known partial coverage, and the run correlation ID.

## 7. Required tests and failure behavior

Backend tests prove migration constraints, append-only behavior, project and
reference isolation, role/capability authorization, actor derivation,
idempotency race handling, correction linkage, payload/narrative sanitization,
cursor/filter/search behavior, and automatic graph/Wiki/verification events.
Agent tests prove command validation, no new core-tool registration, capability
advertisement, outbox persistence/replay, idempotent resume, and the rule that
only durable mutation outcomes create entries. Frontend tests prove default
human-readable rendering, filter/search state, Markdown rendering safety,
reference navigation, technical-detail disclosure, and the four graph modes'
truthful empty/unknown/error states. The Symfony Demo acceptance test proves the
end-to-end path in section 6.

If event recording fails after a mutation, the mutation is not rolled back just
to erase the fact. The server returns a typed `logbook_recording_failed` result,
records a durable retryable system obligation where possible, and the agent
surfaces it as a degraded operation. No caller may report the workflow fully
complete until that obligation succeeds or an authorized human records a
decision explaining the exception.

## 8. Explicit non-goals for this slice

- Multi-company/user administration beyond the existing project authorization
  model.
- Editing/deleting historical logbook entries.
- Replacing Wiki content with the logbook or copying every logbook event into
  Wiki pages.
- Full production cutover, v1 retirement, or destructive database/Neo4j work.
- Carnovali as the MVP acceptance fixture.
- A new core model tool, an Inertia surface, a second parser, or a second graph
  renderer.

No entry is written to `LOGBOOK_CARNOVALI`: this is Hades platform work.
