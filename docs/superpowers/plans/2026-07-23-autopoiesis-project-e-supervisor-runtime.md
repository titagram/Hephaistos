# Autopoiesis Project E — Supervisor, Runtime Resolution, Promotion, and Rollback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compare stable and candidate generations through real runtime paths, issue an immutable promotion report, activate only an explicitly approved generation for new sessions, observe it externally, and automatically restore last-known-good on deterministic hard failure.

**Architecture:** A frozen `GenerationResolution` is created when a session is constructed or resumed and is never reread during that session. A generation-scoped runtime owns overlay skills, scripts, plugins, MCP clients, hooks, and tool catalog without mutating process globals. An external host-owned supervisor executes stable/candidate canaries and owns pointer writes. Promotion is a write-ahead ledger transaction plus compare-and-swap pointer protocol. Existing sessions remain pinned; unsafe sessions are stopped/restarted, never hot-mutated.

**Tech Stack:** Project A ledger/store/pointers/lock, Project D sandbox/adapters, existing skill/plugin/MCP loaders refactored for explicit scoped instances, contextvars propagated by `agent/thread_context.py`, SessionDB migration, pytest multiprocessing/failure injection.

## Global Constraints

- Read design sections “Canary Supervisor”, “Runtime Generation Resolver”,
  “Canary Design”, “Promotion, Observation, and Rollback”, and “Failure and
  Recovery Semantics”.
- Candidate code cannot write lifecycle state, choose canary commands, mark a
  check passed, generate authoritative reports, activate itself, or disable
  rollback.
- A skipped, stale, unavailable, indeterminate, partial, unsigned, or
  unverifiable mandatory check is failure.
- Promotion/rollback affect only new sessions. Existing session prompt/tool
  bytes and component runtime remain pinned.
- Automatic rollback uses only the closed hard-trigger enum. Subjective quality
  or cost drift without an approved hard ceiling can block stabilization but
  cannot auto-rollback.
- If neither pointer proves a compatible generation, use stable base with all
  overlays disabled and surface a critical diagnostic.

---

## File and Module Map

| Path | Responsibility |
|---|---|
| `hermes_cli/evolution/resolver.py` | Pointer/store/ledger resolution and exact-generation resume |
| `hermes_cli/evolution/runtime.py` | Generation runtime cache and reference-counted leases |
| `hermes_cli/evolution/runtime_context.py` | Context-local pinned runtime during init/turns |
| `hermes_cli/evolution/tool_catalog.py` | Base-plus-overlay schema and dispatch |
| `hermes_cli/evolution/canary.py` | Stable/candidate mandatory matrix |
| `hermes_cli/evolution/report.py` | Content-addressed promotion report |
| `hermes_cli/evolution/supervisor.py` | Canary, activation, observation, hard rollback |
| `hermes_cli/evolution/promotion.py` | CAS/write-ahead pointer protocol |
| `hermes_cli/evolution/health.py` | Hard triggers, heartbeats, unsafe-session records |
| `tools/registry.py`, `model_tools.py` | Explicit scoped catalog seams |
| `hermes_cli/plugins.py`, `tools/mcp_tool.py` | Instance/scoped runtime seams |
| `agent/skill_utils.py`, `agent/skill_commands.py` | Generation-aware explicit roots/caches |
| `agent/agent_init.py`, `agent/tool_executor.py`, `run_agent.py` | Pin and use one resolution |
| `hermes_state.py`, `gateway/session.py`, `tui_gateway/server.py` | Persist generation ID |

## Task 1: Frozen resolution and persistent session pinning

**Model:** `gpt-5.6-sol`, reasoning `xhigh`

**Objective:** Define the one-time resolution boundary and prove an existing or
resumed session cannot drift when pointers change.

**Non-goals:** No overlay plugin/MCP load and no pointer mutation.

**Files:**
- Create: `hermes_cli/evolution/resolver.py`
- Create: `hermes_cli/evolution/runtime_context.py`
- Modify: `hermes_state.py`
- Modify: `gateway/session.py`
- Modify: `agent/agent_init.py`
- Modify: `run_agent.py`
- Modify: `tui_gateway/server.py`
- Create: `tests/hermes_cli/evolution/test_resolver.py`
- Create: `tests/run_agent/test_generation_pinning.py`
- Modify: `tests/gateway/test_session_store.py`
- Create: `tests/tui_gateway/test_generation_pinning.py`

**Context pack:** Project A pointer/reconcile/store; AIAgent init/signature;
SessionDB create/resume; gateway/TUI session constructors; design session rules.

**Interfaces:**

```python
def resolve_active_generation(*, repair: bool = False) -> GenerationResolution: ...
def resolve_generation(generation_id: str, *,
                       expected_manifest_digest: str | None = None) -> GenerationResolution: ...
def resolution_for_session(session_id: str, *,
                           persisted_generation_id: str | None) -> GenerationResolution: ...

@contextmanager
def bind_generation_runtime(runtime: GenerationRuntime | None) -> Iterator[None]: ...
```

SessionDB adds nullable `generation_id TEXT` and
`generation_manifest_digest TEXT`; new sessions set both before the first
model call. Legacy sessions with neither field resolve the active generation
once and persist it. A session with only one field is incoherent and requires
safe restart.

- [ ] **Step 1: Write the resolution truth table**

Cover coherent active; corrupt active/valid LKG; missing generation; manifest
digest mismatch; stable-base compatibility mismatch; both invalid; legacy
session; exact historical resume; unsafe generation; future schema.

- [ ] **Step 2: Write failing session-drift tests**

Create Agent A, change active pointer, create Agent B, then rebuild prompts,
compress context, and execute another turn on A. A keeps old generation ID and
byte-identical system/tool prefix; B sees new ID.

- [ ] **Step 3: Write failing concurrent gateway/TUI tests**

Two sessions created around a pointer switch keep independent IDs and survive
process cache reuse. Resume reads persisted identity, not current active.

- [ ] **Step 4: Run red tests**

Run the new resolver/run-agent/gateway/TUI tests.

- [ ] **Step 5: Implement read-only resolution**

Validate pointer, ledger transition, manifest, every declared component, and
stable compatibility. Return only frozen paths under the verified generation.
Do not cache `active.json` as session identity.

- [ ] **Step 6: Bootstrap only before new-session resolution**

The trusted agent host calls Project A's `ensure_evolution_initialized()` before
resolving a brand-new or legacy-unpinned session. Exact-generation resume never
bootstraps or substitutes another generation. The resolver itself stays
read-only, so tests can distinguish initialization from resolution.

- [ ] **Step 7: Persist before model-visible assembly**

Set `agent.generation_id`, manifest digest, and resolution before skill prompt
or tools are assembled. If persistence fails, do not start a model turn with
an unrecorded overlay.

- [ ] **Step 8: Bind context for every prompt/turn path**

Bind during agent initialization, compression prompt rebuild, normal turn,
parallel tool workers, and resume. Rely on existing thread-context propagation;
always reset tokens in `finally`.

- [ ] **Step 9: Run tests repeatedly and commit**

Run the task matrix ten times with concurrent tests.

Commit: `evolution-e: pin sessions to verified generations`

**Escalate if:** any code rereads active pointer mid-session, a resumed session
silently migrates, or prompt/tool bytes differ without a new session.

## Task 2: Scoped tool catalog and plugin-manager seam

**Model:** `gpt-5.6-terra`, reasoning `high`

**Objective:** Allow a pinned generation to add tools/hooks without modifying
the process-global base registries used by other sessions.

**Non-goals:** No MCP transport yet and no activation.

**Files:**
- Create: `hermes_cli/evolution/tool_catalog.py`
- Modify: `tools/registry.py`
- Modify: `model_tools.py`
- Modify: `hermes_cli/plugins.py`
- Modify: `agent/tool_executor.py`
- Modify: `agent/agent_runtime_helpers.py`
- Create: `tests/hermes_cli/evolution/test_tool_catalog.py`
- Create: `tests/hermes_cli/evolution/test_scoped_plugins.py`
- Modify: `tests/tools/test_registry.py`
- Modify: `tests/tools/test_tool_search.py`
- Modify: `tests/hermes_cli/test_plugins.py`
- Modify: `tests/agent/test_tool_executor.py`

**Context pack:** Resolver/runtime context; registry/plugin public APIs and
dispatch call sites; Project D plugin adapter declarations.

**Interfaces:**

```python
class ScopedToolCatalog:
    generation_id: str
    def register_overlay(self, entry: ToolEntry) -> None: ...
    def get_definitions(self, *, enabled_toolsets, disabled_toolsets,
                        quiet: bool) -> list[dict]: ...
    def get_entry(self, name: str) -> ToolEntry | None: ...
    def dispatch(self, name: str, args: dict, **kwargs) -> str: ...
    @property
    def fingerprint(self) -> str: ...

class PluginManager:
    def __init__(self, *, tool_registry: ToolRegistry | None = None,
                 scoped_generation_id: str | None = None): ...
```

- [ ] **Step 1: Write failing collision/isolation tests**

Overlay registration cannot override base or another generation. Two
simultaneous session catalogs may expose different same-purpose tools without
cross-dispatch. Base definitions/generation counter remain unchanged.

- [ ] **Step 2: Write failing hook isolation tests**

Global policy hooks run for every session; generation hooks run only while that
runtime context is bound. Parallel threads do not leak managers. Context reset
removes hooks after the turn.

- [ ] **Step 3: Write failing tool-search/coercion tests**

Tool search/describe/call, schema coercion, max-result size, middleware, pre/
post hooks, and `execute_code` allowlist all use the same scoped catalog.

- [ ] **Step 4: Run red tests**

Run new tests plus registry, plugin, tool-search bridge, and parallel executor
tests.

- [ ] **Step 5: Implement catalog composition**

Snapshot base entries at runtime creation, add verified overlay entries, reject
name/toolset collisions, and cache definitions by catalog fingerprint plus
existing config/toolset inputs.

- [ ] **Step 6: Parameterize plugin registration**

`PluginContext.register_tool` targets its manager's registry. Default singleton
behavior remains global. A scoped manager loads only explicit verified
standalone plugin paths; it rejects overrides, CLI/platform/provider/exclusive
registrations, and any declaration absent from the manifest.

- [ ] **Step 7: Route dispatch through bound catalog**

Use explicit agent catalog where available and current default otherwise.
Audit every `handle_function_call` recursion and helper path.

- [ ] **Step 8: Run regressions and commit**

Commit: `evolution-e: isolate generation tool and plugin catalogs`

**Escalate if:** a generation bumps the global registry, a hook leaks across
sessions, or tool search can call outside the pinned catalog.

## Task 3: Generation-aware skill and script roots

**Model:** `gpt-5.6-terra`, reasoning `medium`

**Objective:** Make verified overlay skills discoverable in one pinned session
and keep slash-command/prompt caches generation-specific.

**Non-goals:** No global `skills.external_dirs` edit or hot reload into an
existing session.

**Files:**
- Modify: `agent/skill_utils.py`
- Modify: `agent/skill_commands.py`
- Modify: `agent/prompt_builder.py`
- Modify: `tools/skills_tool.py`
- Modify: `tools/skill_usage.py`
- Modify: `cli.py`
- Modify: `gateway/run.py`
- Modify: `tui_gateway/server.py`
- Create: `tests/hermes_cli/evolution/test_overlay_skills.py`
- Modify: `tests/agent/test_external_skills.py`
- Modify: `tests/agent/test_skill_commands.py`
- Modify: `tests/agent/test_skill_commands_reload.py`
- Modify: `tests/agent/test_skill_utils.py`

**Context pack:** Resolver roots; current scanner/prompt/cache consumers;
Project D skill/script manifests.

**Interfaces:**

```python
def get_all_skills_dirs(*, overlay_roots: Iterable[Path] = ()) -> list[Path]: ...
def scan_skill_commands(*, overlay_roots: Iterable[Path] = (),
                        cache_key: str | None = None) -> dict[str, dict]: ...
```

- [ ] **Step 1: Write failing precedence and pinning tests**

User local skill precedence remains first, then configured external roots, then
verified generation roots. Duplicate names are deterministic and a generation
cannot shadow a protected built-in/user skill without explicit non-MVP policy.

- [ ] **Step 2: Write failing cache tests**

Two generation IDs produce separate skill-command maps and prompt indices.
Promotion does not invalidate the old session cache. Compression rebuild uses
the old key.

- [ ] **Step 3: Run red tests**

Run overlay plus existing skill scanner/external dir/reload tests.

- [ ] **Step 4: Add explicit roots, not config mutation**

All existing callers keep defaults. Agent/session callers pass frozen roots
from runtime context. Verify every root is beneath the resolved generation and
manifest-listed.

- [ ] **Step 5: Keep scripts relative and immutable**

Skill instructions may reference scripts only inside their component/generation
root. Runtime resolves the path from the pinned generation, never from active
pointer or current working directory.

- [ ] **Step 6: Run tests and commit**

Commit: `evolution-e: scope overlay skills to pinned sessions`

**Escalate if:** a scanner uses global active state, reload affects an existing
session, or a script path escapes the generation.

## Task 4: Generation-scoped plugin and MCP runtime leases

**Model:** `gpt-5.6-terra`, reasoning `high`

**Objective:** Load verified plugin and MCP components once per generation,
share them safely across pinned sessions, and release them only after all
leases end.

**Non-goals:** No promotion or canary.

**Files:**
- Create: `hermes_cli/evolution/runtime.py`
- Modify: `tools/mcp_tool.py`
- Modify: `hermes_cli/plugins.py`
- Modify: `agent/agent_init.py`
- Modify: session finalization/reset paths in CLI/gateway/TUI
- Create: `tests/hermes_cli/evolution/test_runtime_leases.py`
- Create: `tests/hermes_cli/evolution/test_scoped_mcp.py`
- Modify: focused MCP dynamic discovery/reload/shutdown tests

**Context pack:** Tasks 1–3; MCP global state/register/shutdown paths; plugin
manager lifecycle; Project D verified declarations.

**Interfaces:**

```python
class GenerationRuntimeCache:
    def acquire(self, resolution: GenerationResolution) -> GenerationRuntimeLease: ...
    def mark_unsafe(self, generation_id: str, reason_code: str) -> None: ...
    def shutdown_unused(self) -> None: ...

class McpRuntime:
    def discover(self, configs: tuple[Path, ...],
                 registry: ToolRegistry) -> tuple[str, ...]: ...
    def shutdown(self) -> None: ...
```

- [ ] **Step 1: Write failing refcount/concurrency tests**

Same generation loads once, two sessions hold two leases, closing one does not
stop the other, closing the last cleans MCP/process/plugin resources, and a
new generation gets a separate namespace/state.

- [ ] **Step 2: Write failing rollback pinning tests**

Marking a generation unsafe prevents new acquisition but does not mutate or
unload a live old session underneath a turn. That session is separately
stopped by Task 8's unsafe-session guard.

- [ ] **Step 3: Write failing MCP isolation tests**

Different generations may expose different fixture servers/tools. Global MCP
status/refresh/shutdown does not mutate scoped clients; scoped shutdown does
not touch global servers. A promoted runtime may resolve only the credential
reference names declared in its manifest through the existing host credential
resolver; missing references make that server unavailable without revealing
the value, and candidate/canary runtimes always receive the side-effect-denied
fixture resolver instead of production credentials.

- [ ] **Step 4: Run red tests**

Run new tests and focused MCP/plugin lifecycle tests.

- [ ] **Step 5: Instance-parameterize MCP state**

Move server/client maps and locks behind an `McpRuntime` instance while keeping
the current module-level default for existing callers. Register discovered
tools into the scoped catalog only after manifest/schema comparison. Inject
resolved secrets directly into the declared server process environment and
never into the catalog, manifest, report, ledger, diagnostic, or model-visible
tool result.

- [ ] **Step 6: Implement generation-qualified plugin namespaces**

Use generation digest plus plugin key in module names. Verify on-disk identity
before load, compare actual registrations with Project D evidence, and fail the
runtime if they differ.

- [ ] **Step 7: Acquire/release at agent lifecycle boundaries**

Agent initialization acquires before prompt/tool assembly. Final session
reset/expiry/shutdown releases exactly once; per-turn finalization does not.

- [ ] **Step 8: Run tests and commit**

Commit: `evolution-e: lease generation plugin and mcp runtimes`

**Escalate if:** MCP/plugin state remains global, promotion unloads an old
session, or actual registrations differ from the verified manifest.

## Task 5: External stable-versus-candidate canary

**Model:** `gpt-5.6-terra`, reasoning `high`

**Objective:** Execute the Blueprint's mandatory matrix against parent and
candidate through real discovery/invocation paths under host-owned policy.

**Non-goals:** No report approval or pointer write.

**Files:**
- Create: `hermes_cli/evolution/canary.py`
- Create: `hermes_cli/evolution/supervisor.py`
- Create: `tests/hermes_cli/evolution/test_canary.py`
- Create: `tests/hermes_cli/evolution/test_canary_policy.py`
- Create: `tests/fixtures/autopoiesis/canary/` fixture inputs/expected results

**Context pack:** Project D BuildAuthority/SandboxRunner and candidate Gnothi;
Blueprint canary policy; resolver exact-generation API.

**Interfaces:**

```python
CheckStatus = Literal["passed", "failed", "indeterminate"]

@dataclass(frozen=True)
class CanaryCheck:
    check_id: str
    mandatory: bool
    kind: str
    repetitions: int
    timeout_seconds: int
    allowed_side_effects: tuple[str, ...]

def run_canary(generation_id: str) -> CanaryRunResult: ...
```

- [ ] **Step 1: Write failing classification tests**

Skipped/unavailable/stale/partial/missing evidence is `indeterminate`; a
mandatory indeterminate makes the run fail. Candidate self-report cannot
override supervisor observation.

- [ ] **Step 2: Write the complete matrix tests**

Stable reproduction, candidate target success, common invariants, changed-class
real load/invoke, startup/heartbeat/shutdown/crash, resources, forbidden
access, candidate Gnothi diff, compatibility, and deterministic repetitions.

- [ ] **Step 3: Run red tests**

Run new canary tests.

- [ ] **Step 4: Implement supervisor-owned execution**

The supervisor selects commands from validated Blueprint/check types, creates
fresh controlled fixture copies, runs stable and candidate separately, hashes
inputs/outputs, and classifies evidence itself.

- [ ] **Step 5: Enforce side-effect denial and resource ceilings**

Any forbidden attempt is a failed policy check even if target output is
correct. Preserve bounded evidence references and terminate all resources.

- [ ] **Step 6: Transition state**

Under the lifecycle lock, `quarantined -> canary_running`; success data is
recorded but only Task 6's verified report may enter `promotion_ready`.
Failure enters `canary_failed`.

- [ ] **Step 7: Run tests and commit**

Commit: `evolution-e: compare candidate with external canary`

**Escalate if:** a candidate selects tests, missing evidence passes, or stable
and candidate do not receive equivalent controlled inputs.

## Task 6: Promotion report and exact approval request

**Model:** `gpt-5.6-terra`, reasoning `medium`

**Objective:** Create a content-addressed report from verified canary evidence
and request promotion of exactly that generation/report/current sequence.

**Non-goals:** No pointer switch.

**Files:**
- Create: `hermes_cli/evolution/report.py`
- Modify: `hermes_cli/evolution/supervisor.py`
- Modify: `hermes_cli/evolution/command.py`
- Modify: `hermes_cli/subcommands/evolution.py`
- Create: `tests/hermes_cli/evolution/test_promotion_report.py`
- Create: `tests/hermes_cli/evolution/test_promotion_request.py`

**Context pack:** Canary result; Project A promotion scope; design report field
list.

**Interfaces:**

```python
def build_promotion_report(run_id: str) -> PromotionReport: ...
def report_digest(report: PromotionReport) -> str: ...
def request_promotion_approval(report_digest: str) -> AuthorizationRequest: ...
```

- [ ] **Step 1: Write failing report completeness tests**

Require every design field, all check statuses/repetitions/variance, resource/
policy observations, expected/actual organism diff, zero material deviations,
bounded evidence refs, verifier versions, recommendation, and rollback target.

- [ ] **Step 2: Write failing tamper/staleness tests**

Changing any report fact changes digest. A newer canary, changed generation,
changed active sequence, or changed rollback target invalidates the request.

- [ ] **Step 3: Run red tests**

Run both new test files.

- [ ] **Step 4: Implement host-only report verification/publication**

Reopen all evidence, verify HMAC/content digests and generation bytes, build
canonical JSON, publish under `reports/<digest>.json`, reopen, and then append
the report event.

- [ ] **Step 5: Enter `promotion_ready` and request approval**

Only a report with every mandatory check passed and no material deviations may
transition. Promotion scope includes generation, report, expected active ID,
expected sequence, and exact switch operation.

- [ ] **Step 6: Add `canary`, `show report`, and `request-promotion` commands**

Approval uses Project C's host-owned surface. There is still no implicit
promotion.

- [ ] **Step 7: Run tests and commit**

Commit: `evolution-e: bind promotion to verified canary report`

**Escalate if:** report trusts candidate JSON or approval omits current active
identity/sequence.

## Task 7: Atomic promotion and deterministic hard rollback

**Model:** `gpt-5.6-sol`, reasoning `xhigh`

**Objective:** Implement the only authoritative non-baseline pointer switches,
including every crash boundary and automatic restoration.

**Non-goals:** No quality-based rollback and no live session mutation.

**Files:**
- Create: `hermes_cli/evolution/promotion.py`
- Modify: `hermes_cli/evolution/pointers.py`
- Modify: `hermes_cli/evolution/reconcile.py`
- Modify: `hermes_cli/evolution/supervisor.py`
- Create: `tests/hermes_cli/evolution/test_promotion.py`
- Create: `tests/hermes_cli/evolution/test_rollback.py`
- Create: `tests/hermes_cli/evolution/test_pointer_crash_matrix.py`
- Create: `tests/hermes_cli/evolution/test_promotion_concurrency.py`

**Context pack:** Project A lock/pointer/reconcile/event chain; Task 6 grant/
report; exact design promotion sequence and hard triggers.

**Write-ahead protocol:**

1. Acquire lifecycle lock and consume exact promotion grant.
2. Verify expected active ID/sequence, candidate, report, and LKG.
3. Append a `pointer_transition_prepared` event containing old/new document
   digests and one new lifecycle sequence.
4. Atomically write LKG = old active, bound to the prepared event.
5. Atomically write active = candidate, bound to the same prepared event.
6. Reopen and verify both files plus all referenced bytes.
7. Append `pointer_transition_committed` referencing the prepared event.
8. Transition attempt to `active`.

The resolver accepts the new active only when the prepared event has exactly
one matching committed event. Reconciliation after interruption either proves
and completes the prepared transition or restores the exact old pointer; it
never guesses from mtimes.

- [ ] **Step 1: Write the full crash matrix before implementation**

Inject process death before/after each of the eight boundaries, including file
fsync, rename, directory fsync, reread, and commit append. For every case assert
the active generation after reconciliation and complete event explanation.

- [ ] **Step 2: Write concurrent promotion/rollback tests**

Two promotions, promotion versus manual rollback, and hard rollback versus
stabilization race. Exactly one CAS wins; no lost event or split active/LKG
state is accepted. Promotion is rejected unless the current active generation
is a proven healthy/stable rollback target; an `active` generation still inside
its own observation window cannot be overwritten and designated LKG.

- [ ] **Step 3: Write the closed hard-trigger tests**

Only digest mismatch, pointer/ledger mismatch, resolver load failure, repeated
startup/crash, repeated heartbeat loss, side-effect violation, compatibility
mismatch, and loss of recovery control may invoke automatic rollback.

- [ ] **Step 4: Run red tests**

Run all four new test files.

- [ ] **Step 5: Implement promotion under one lock**

Revalidate after acquiring the lock. Consume the grant in the prepare
transaction. Use compare-and-swap on expected sequence and explicit event
digests. Never let the candidate process call this function.

- [ ] **Step 6: Implement rollback as a new committed transition**

Rollback does not rewind history. It prepares/commits active = proven LKG,
records failed generation/trigger/evidence, marks the failed generation unsafe,
invalidates stabilization, and leaves bytes inspectable.

- [ ] **Step 7: Implement manual rollback**

Allow only a prior healthy generation compatible with current stable base.
Rolling forward later still requires new current canary/report/promotion grant.

- [ ] **Step 8: Run crash/concurrency tests repeatedly**

Run the four test files twenty times, then all Project A pointer/reconcile
tests.

- [ ] **Step 9: Commit**

Commit: `evolution-e: atomically promote and restore generations`

**Escalate if:** any crash leaves an accepted ambiguous state, a grant is
reusable after CAS failure, or rollback uses a subjective signal.

## Task 8: Observation window, unsafe sessions, notices, and operator lifecycle

**Model:** `gpt-5.6-terra`, reasoning `high`

**Objective:** Observe newly active generations externally, stabilize only
after healthy evidence, stop unsafe pinned sessions visibly, and expose
operator lifecycle commands.

**Non-goals:** No hot removal/replacement of tools or prompt content.

**Files:**
- Create: `hermes_cli/evolution/health.py`
- Modify: `hermes_cli/evolution/supervisor.py`
- Modify: `agent/turn_context.py`
- Modify: `agent/turn_finalizer.py`
- Modify: `hermes_cli/evolution/notices.py`
- Modify: `hermes_cli/evolution/command.py`
- Modify: `hermes_cli/subcommands/evolution.py`
- Create: `tests/hermes_cli/evolution/test_observation.py`
- Create: `tests/run_agent/test_unsafe_generation.py`
- Create: `tests/hermes_cli/evolution/test_supervisor_command.py`

**Context pack:** Task 7 active/unsafe events; AgentNotice rail; turn entry/
finalization; config defaults.

**Interfaces:**

```python
def check_session_generation(generation_id: str) -> SessionSafetyDecision: ...
def record_runtime_health(event: RuntimeHealthEvent) -> None: ...
def evaluate_observation_window(generation_id: str) -> ObservationDecision: ...
def supervisor_tick() -> SupervisorTickResult: ...
```

- [ ] **Step 1: Write failing unsafe-session tests**

An unsafe pinned session is interrupted before another model/tool call and gets
a visible restart-required diagnostic. Its tools/system prompt are not edited.
A session pinned to a healthy historical generation remains usable unless
explicitly unsafe.

- [ ] **Step 2: Write failing stabilization tests**

Require the configured window, minimum successful new-session starts,
heartbeats, no hard trigger, and no unresolved mandatory quality warning.
Quality warnings block `stable` and notify but do not auto-rollback.

- [ ] **Step 3: Write failing monitor tests**

The host scheduler/service can detect repeated startup/crash/heartbeat
thresholds across process restarts from ledger events. Candidate heartbeat
claims alone are insufficient.

- [ ] **Step 4: Run red tests**

Run new observation/unsafe/supervisor tests.

- [ ] **Step 5: Add safe turn-entry guard**

Read only the pinned generation's unsafe status before each turn. Return a
driver-visible termination/restart result without adding a synthetic
conversation message or changing cached schema.

- [ ] **Step 6: Implement host monitor**

Provide `evolution supervisor run [--once]` and start the same bounded
fail-soft monitor in long-lived CLI/gateway processes when evolution is
enabled. It owns no model and uses the lifecycle lock for mutations.

- [ ] **Step 7: Add complete operator commands**

Expose `status`, `canary`, `approve-promotion`, `history`, `rollback`,
`supervisor`, and report/generation views with JSON and dry-run where
applicable. Automatic rollback emits a critical `AgentNotice` with generation,
trigger code, and restored ID only.

- [ ] **Step 8: Run tests and commit**

Commit: `evolution-e: observe active generations and stop unsafe sessions`

**Escalate if:** unsafe handling edits a live schema, monitor trusts candidate
health, or a quality warning causes hard rollback.

## Task 9: Independent activation, rollback, and cache-safety gate

**Model:** `gpt-5.6-sol`, reasoning `xhigh`

**Objective:** Prove real session isolation, all atomic boundaries, rollback
control, and the absence of cross-generation/global leaks before the pilot.

**Non-goals:** No authentic pilot claim yet.

**Files:**
- Create: `tests/integration/test_autopoiesis_runtime_generations.py`
- Create: `tests/integration/test_autopoiesis_promotion_recovery.py`
- Create: `tests/integration/test_autopoiesis_supervisor.py`
- Create: `docs/autopoiesis/supervisor-v1.md`

No production files are planned in this verification task. A reproduced defect
must become a focused failing regression and a separate repair task before the
gate is rerun.

**Context pack:** All Project E diffs/tests; Project D generation fixture;
prompt caching/role alternation contracts; acceptance criteria 8–16, 18, 20.

- [ ] **Step 1: Build three simultaneous real sessions**

Start old-active Session A, promote candidate, start Session B, trigger rollback,
then start Session C. Assert A=old, B=candidate and later visibly unsafe,
C=old LKG. Compare exact prompt/tool/skill/plugin/MCP fingerprints per session.

- [ ] **Step 2: Exercise real component paths**

Through actual Agent construction and tool dispatch, use candidate skill,
plugin tool, script invocation, and fixture MCP tool in B. Assert A/C cannot
discover or invoke them.

- [ ] **Step 3: Re-run every pointer crash boundary in subprocesses**

Kill real processes, restart with reconciliation, and compare ledger history,
pointer bytes, active resolution, and generation store integrity.

- [ ] **Step 4: Attack the authority boundary**

From candidate/plugin/MCP fixture processes attempt report tamper, grant issue,
pointer write, ledger write, active switch, health declaration, global registry
mutation, and supervisor shutdown. None may succeed.

- [ ] **Step 5: Verify cache and alternation**

Record system prompt bytes and tool schema canonical bytes before/after
promotion/rollback/compression for A and B. Inspect persisted roles; no
synthetic user/system message is introduced.

- [ ] **Step 6: Run the complete matrix**

```bash
scripts/run_tests.sh tests/hermes_cli/evolution -q
scripts/run_tests.sh tests/integration/test_autopoiesis_*.py -q
scripts/run_tests.sh tests/hermes_cli/engineering_review -q
scripts/run_tests.sh tests/hermes_cli/test_gnothi_*.py -q
scripts/run_tests.sh tests/tools/test_mcp_*.py tests/hermes_cli/test_plugins.py -q
```

- [ ] **Step 7: Document state diagrams/recovery proof and commit**

Commit: `evolution-e: prove generation-safe promotion and rollback`

Handoff the exact commands for one canary, approval, promotion, failure
injection, rollback, and three-session proof to Project F.

**Escalate if:** any global state differs after scoped load, a crash outcome is
not uniquely explained, or an existing session observes a hot change.
