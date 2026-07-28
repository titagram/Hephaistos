# Evolution Control Center Design

**Date:** 2026-07-28
**Status:** Approved design, pending document review
**Parent designs:**

- `2026-07-11-gnothi-seauton-autopoiesis-design.md`
- `2026-07-22-autopoiesis-mvp-design.md`
- `2026-07-24-autopoiesis-project-b-opportunity-observer-design.md`
- `2026-07-25-autopoiesis-proposal-blueprint-design.md`

**Selected visual direction:** organism graph explorer, concept 2
**Visual reference:** `assets/evolution-control-center-organism-concept-v2.png`

## Summary

Add a bundled dashboard plugin named `evolution` at `/evolution`. It gives the
operator one graphical control center for Gnothi Seauton and Autopoiesis while
preserving their defining property: they describe and evolve one local agent,
not a shared remote Hades project.

The page has four persistent views:

1. Overview
2. Organism
3. Telos
4. Pipeline

The Organism view is the visual signature and the default view. It renders the
evidence-backed Gnothi graph, its coverage, drift, dependencies, blockers, and
revision history. The other views expose the local readiness state, Telos
governance, Observer suggestions, blueprint creation, and the implemented
evolution lifecycle.

Online public research is always available to the agent. Network access is not
an evolution authorization. Evolution authorizations govern state changes:
advancing a suggestion, building a candidate, activating a Telos revision, or
promoting and rolling back a generation.

## Product Principles

### One agent, one local organism

Each installation owns one immutable `organism_id`, one lineage root, one
global Telos history, one evolution ledger, and one global Gnothi history.
Different installations may evolve differently and must not silently converge,
merge, or inherit each other's state.

Profiles are operational facets of the same agent. Selecting `default`, `leaf`,
`reviewer`, `orchestrator`, or another local profile must never switch the
organism shown by the dashboard.

Profile-local experience may contribute sanitized evidence to the global
Observer. It does not create a separate organism or change organism identity.

### Local state, online knowledge

The following are local-only:

- organism identity and lineage;
- Gnothi artifacts and revisions;
- Telos drafts, revisions, and active pointer;
- lifecycle ledger and authorization facts;
- Observer events and suggestions;
- blueprints, candidate generations, reports, and audit history.

The dashboard must not synchronize, replicate, compare, upload, or merge this
state through the remote Hades backend.

The following public, read-only online operations are always permitted:

- web search;
- opening public documentation;
- reading public standards and implementation references;
- fetching public documentation artifacts for local analysis.

Research queries must not contain secrets, private memory, raw logs, private
configuration, unredacted organism artifacts, private source, or user content.
Research results are summarized and stored locally with source references.
Always-on research does not permit authenticated browsing, form submission,
file upload, package installation, executable downloads, or execution of
retrieved content. HTTP access remains bounded by time, size, scheme, and
redirect limits.

`research_authorized` remains a lifecycle fact: it says that a specific
evolution attempt may formally use research to advance. It is not a network
permission and must not be presented as one in the UI.

### Govern mutations, not learning

Gnothi reads are always safe. Public research is always available. State
changes remain governed:

- organism rebuilds are explicit, read-only collection jobs that publish a new
  immutable revision;
- Telos drafts are inert;
- Telos activation and rollback require strong confirmation;
- a suggestion must still be eligible before a blueprint can be created;
- build and promotion require their own authorizations when those runtime
  stages exist;
- existing sessions never change prompt or tool schema after promotion.

## Scope

### Included

- a bundled dashboard extension with a `/evolution` tab;
- local authenticated API routes for current Gnothi and Autopoiesis services;
- a global, cross-profile organism identity header;
- explicit local organism initialization when no identity exists;
- Overview, Organism, Telos, and Pipeline views;
- organism rebuild and revision comparison;
- Observer pause, resume, status, and scan;
- public online research entry points without a network grant dialog;
- Telos draft authoring, history, diff, activation, and rollback;
- suggestion inspection and blueprint creation;
- local audit history and background-job progress;
- coherent empty, partial, stale, degraded, and corrupted states;
- responsive desktop and compact layouts;
- behavioral, integration, browser, accessibility, and visual tests.

### Not included

- remote Hades backend synchronization;
- organism import, merge, or cross-agent comparison;
- automatic identity replacement from an export;
- a second source of truth for lifecycle state;
- arbitrary shell command execution from the browser;
- fake controls for build, canary, promotion, or rollback stages that are not
  implemented by the local runtime;
- core model tools or conversation-time tool-schema mutation;
- a replacement for the CLI and `/autopoiesis` conversational workflow.

## Architecture

### Bundled dashboard plugin

The feature lives at the product edge as a bundled dashboard plugin, following
the existing Kanban pattern:

```text
plugins/evolution/dashboard/
  manifest.json
  plugin_api.py
  src/
  dist/index.js
  dist/style.css
```

The manifest registers:

```json
{
  "name": "evolution",
  "label": "Evolution",
  "tab": {
    "path": "/evolution",
    "position": "after:plugins"
  },
  "entry": "dist/index.js",
  "css": "dist/style.css",
  "api": "plugin_api.py"
}
```

The dashboard shell remains responsible for navigation, session
authentication, theme, profile selection, and plugin loading. The plugin uses
the public dashboard SDK and Nous UI primitives. It must not bundle another
React runtime.

### Local API adapter

`plugin_api.py` is a thin authenticated adapter. It calls the existing Python
domain services in-process and does not run user-constructed CLI strings.

The adapter resolves all organism-level state from
`hermes_constants.get_organism_home()`. It must not derive organism identity
from the currently selected dashboard profile.

Read endpoints never initialize or repair organism state. An uninitialized
agent returns a missing state and offers an explicit local initialization
action.

The current Gnothi command path still has profile-local storage behavior in
`hermes_cli.gnothi.store.OrganismRevisionStore`. Implementation must consolidate
the dashboard and canonical CLI/service path on the global organism root before
claiming cross-profile correctness. Compatibility reads or migration may be
needed, but profile-local stores must never be silently merged.

### Snapshot contract

The primary read endpoint returns one bounded consistent snapshot:

```text
GET /api/plugins/evolution/snapshot
```

The response includes:

- public organism ID prefix, never the complete ID unless needed for a strong
  confirmation;
- lineage root digest prefix;
- active generation and last-known-good generation;
- current Gnothi revision and digest;
- coverage, drift, unknown domains, and collector status;
- active Telos digest and revision summary;
- Observer state and degraded reason;
- suggestion and blueprint counts by state;
- pending approval counts;
- snapshot digest and observation timestamp.

Detail endpoints return bounded data for graphs, revisions, Telos, suggestions,
blueprints, and audit events. All payloads use public local IDs and sanitized
evidence metadata.

Graph reads are bounded subgraph queries rather than unbounded organism dumps.
They accept stable filters, root IDs, and depth, and return total counts plus a
`truncated` marker. The UI expands a selected neighborhood on demand and never
implies that a truncated graph is complete.

### Background jobs

Organism rebuilds, Observer scans, revision diffs, exports, and future research
sessions may exceed an HTTP request budget. They run as bounded local jobs:

```text
POST /api/plugins/evolution/jobs/organism-rebuild
POST /api/plugins/evolution/jobs/observer-scan
GET  /api/plugins/evolution/jobs/{job_id}
```

Jobs expose queued, running, completed, failed, and cancelled states with
bounded progress and redacted errors. A job cannot mutate active Telos or
generation pointers as a side effect.

Polling is sufficient for the first version. A later WebSocket may be added
only if polling produces a demonstrated problem.

## Information Architecture

### Shared shell

All four views share:

- page title `Evolution`;
- organism ID and lineage prefixes;
- internal navigation: `Overview`, `Organism`, `Telos`, `Pipeline`;
- a status rail for revision, generation, coverage, drift, and health;
- non-destructive error banners;
- current background-job status.

The profile selector remains visible because it belongs to the dashboard shell,
but Evolution shows a persistent `Local organism · all profiles` label. Profile
switches may refresh profile facets, never identity or global lifecycle state.

### Overview

Overview answers: “Can this agent understand and safely evolve itself now?”

It contains:

- one readiness summary;
- ordered blockers with direct navigation to the relevant view;
- coverage by Gnothi domain;
- Observer and Telos readiness;
- eligible suggestions and pending decisions;
- recent append-only audit activity.

It has one primary action based on state, such as `Resolve blockers`. It does
not become a grid of unrelated metrics.

### Organism

Organism is the default `/evolution` view and selected visual direction.

The main surface contains:

- filters for capability, runtime, invariant, skill, plugin, and provider;
- search by stable ID or label;
- a legend for `provides`, `requires`, and `depends_on`;
- health states for healthy, degraded, stale, missing, and unknown;
- pan, zoom, fit, and keyboard navigation;
- a graph/list toggle that exposes the same nodes and relationships in an
  accessible structured view;
- one selected-node inspector.

The inspector shows:

- stable ID, label, kind, owner class, and generation scope;
- declared, installed, available, active, verified, degraded, and candidate
  dimensions when present;
- evidence freshness and bounded evidence references;
- direct dependencies and dependents;
- blockers and affected capabilities;
- semantic changes across revisions.

Primary action: `Rebuild organism`. Secondary actions:
`Compare revisions` and `Export wiki`.

When no global Gnothi revision exists, the graph is replaced by a truthful empty
state with the rebuild action. No sample nodes are rendered as live data.

### Telos

Telos contains:

- active revision summary and digest;
- purpose, desired traits, capability directions, priorities, tradeoffs,
  prohibitions, proactivity policy, and success indicators;
- immutable revision history and parent relationships;
- structured draft editor;
- semantic diff between draft, active, and historical revisions;
- strong confirmation for activation and rollback.

Draft saving is not activation. Confirmation shows the target digest, current
digest, organism identity, and consequences. Stale expected digests fail
closed.

### Pipeline

Pipeline shows one selected attempt through:

```text
Suggestion -> Research -> Blueprint -> Build -> Canary -> Promotion -> Stable
```

Implemented stages are interactive. Contracted but unavailable stages remain
visible as unavailable, with an explanation and no fake action.

The view includes:

- an attempt list with score and state;
- suggestion facts and sanitized evidence;
- Telos alignment;
- online research sources and local summaries;
- blueprint content and proposed component classes;
- requested scope and authorization history;
- append-only lifecycle audit.

Online research does not show a permission gate. It may start from the
Pipeline or continue in Chat using the existing agent web capabilities.
Starting research does not authorize build, installation, or promotion.

## Mutation and Concurrency Contract

Every mutation carries:

- organism ID;
- expected snapshot or record digest;
- target local public ID;
- requested operation;
- explicit confirmation proof where required.

The service validates all fields under the existing organism/lifecycle lock.
If any expected digest is stale, the request returns a conflict and makes no
change. The UI refreshes and asks the operator to review the new state.

Strong-confirmation operations include:

- Telos activation;
- Telos rollback;
- future build authorization;
- future promotion;
- future generation rollback or retirement.

Suggestion-to-blueprint creation is allowed only while the suggestion remains
eligible and bound to the active Telos. Equivalent immutable creation is
idempotent.

## Error Handling

Read failures never produce invented state.

- `missing` produces an actionable empty state;
- `partial` preserves valid domains and marks unknown domains;
- `stale` preserves the last verified timestamp;
- `blocked` explains the failed gate;
- `corrupt` hides unsafe detail, disables mutations, and directs the operator
  to local diagnostics;
- network failures affect research only and do not degrade local organism,
  Telos, or ledger health;
- job failures preserve the last coherent snapshot.

The UI keeps the last valid snapshot visible behind a non-destructive warning
when a refresh fails.

## Visual and Interaction Design

The selected concept follows the current Hades dashboard:

- dark textured base surface;
- pale mint primary foreground;
- restrained amber and red for degraded and failed states;
- square corners and thin borders;
- compact technical typography;
- minimal elevation and no nested card grid;
- one dominant continuous graph surface;
- inspector on the right at desktop widths;
- inspector as a drawer in compact layouts.

States must never rely on color alone. Icons, labels, line styles, and textual
status accompany color.

The graph must remain a functional visualization, not a decorative image.
Use a maintained node-graph library compatible with the plugin build and the
host React runtime. The implementation plan must verify dependency footprint,
keyboard behavior, and bundle isolation before selecting the library.

## Security and Privacy

- Dashboard session authentication protects every plugin route.
- WebSocket auth is unnecessary in the polling-based first version.
- No API returns secrets, raw private paths, raw logs, prompts, transcripts,
  cookies, tokens, or unbounded source.
- Public web research never includes private local material in outbound
  requests.
- Research fetches accept only public HTTP(S) resources and never inherit
  dashboard cookies, agent secrets, or authenticated browser state.
- Remote Hades backend clients are not constructed by Evolution routes.
- Organism export is a local read-only artifact; there is no import or merge
  operation in this design.
- Confirmation dialogs cannot be bypassed by calling a broader generic
  endpoint.
- Local immutable artifacts retain their existing ownership, permission,
  digest, symlink, and append-only checks.

## Testing Strategy

### Python and contract tests

- Snapshot fields relate to the same organism ID and committed revision.
- Switching profile overrides does not change organism identity, Telos,
  generation pointers, or global blueprint history.
- Profile-local legacy Gnothi state is never silently merged.
- Mutations with stale digests fail without partial persistence.
- Rebuild and scan jobs are bounded and restart-safe enough to report an
  interrupted result as failed or unknown, never completed.
- Telos draft, activation, rollback, suggestion, and blueprint calls reuse the
  real domain paths.
- Remote backend clients are never created.
- Public research does not require a network authorization record.
- Public research enforces outbound payload redaction and bounded fetch rules.
- Build and promotion remain gated.
- Errors and evidence payloads are redacted and bounded.

### Frontend tests

- All four views render missing, ready, partial, stale, blocked, and corrupt
  states.
- The graph filters and inspector reflect the supplied graph contract.
- Node selection, tab navigation, dialogs, filters, and forms are keyboard
  operable.
- Mutation dialogs show target identity, digest, and consequence.
- Conflict responses refresh rather than retrying automatically.
- Compact mode converts the inspector to a drawer without losing content.

### End-to-end tests

Use a temporary global Hermes root and the real dashboard/plugin discovery
path:

1. open Evolution with no Gnothi revision;
2. rebuild the organism;
3. inspect one real node and its dependencies;
4. switch dashboard profile and prove organism identity is unchanged;
5. create and save an inert Telos draft;
6. exercise strong confirmation against a stale and a current digest;
7. inspect an eligible suggestion and create an idempotent blueprint;
8. perform public research without a network grant while proving no build or
   promotion authorization was created;
9. verify no remote backend request was made.

### Visual QA

Compare the rendered implementation and selected concept at `1440 x 1024`.
Validate typography, borders, spacing, graph density, inspector width, active
navigation, status accents, and empty-state fidelity. Repeat at a compact
desktop width to verify the inspector drawer.

## Acceptance Criteria

1. `/evolution` is discovered as a bundled plugin tab and opens Organism.
2. Read-only opening does not initialize or repair an absent organism.
3. The page always identifies the same local organism across profile switches.
4. Gnothi and Autopoiesis function without a configured remote backend.
5. Public online research is always available without a network grant.
6. Research cannot implicitly authorize or perform an organism mutation.
7. The graph faithfully renders a bounded view of the current immutable Gnothi
   revision and clearly marks truncation.
8. Missing, partial, stale, degraded, and corrupt states are explicit.
9. Supported Telos, Observer, suggestion, and blueprint operations work through
   real local services.
10. Unsupported lifecycle stages expose no fake controls.
11. Sensitive mutations are digest-bound, confirmed, atomic, and audited.
12. No private organism material is uploaded during public research.
13. The implementation matches the selected Hades visual direction and remains
    usable by keyboard and in compact layouts.

## Required Contract Amendment

This design supersedes one policy decision in the parent Autopoiesis designs:
public read-only online research is no longer permission-gated per suggestion.

The parent lifecycle state `research_authorized` and its immutable facts remain
valid as governance over attempt progression. Documentation, skill prompts,
UI copy, and future implementations must stop describing it as the permission
that enables network access.

This amendment does not weaken build, installation, promotion, activation,
rollback, or retirement authorization.
