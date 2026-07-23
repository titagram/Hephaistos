# Autopoiesis Project D — Builder, Quarantine, and A3 Adapters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consume one exact build grant, acquire only approved artifacts into quarantine, validate all four peripheral component classes in isolation, and publish a verified immutable generation plus candidate-scoped organism evidence without changing lifecycle pointers.

**Architecture:** A host-owned `BuildAuthority` validates and consumes the grant, creates a private workspace, and invokes trusted adapters. Acquired or generated component code is untrusted: static checks run first, all imports/execution happen in the existing hardened sandbox path, and candidate output is evidence rather than authority. The host rehashes and publishes exact staged bytes through Project A's store. Adapters never receive the ledger or pointer paths.

**Tech Stack:** Existing engineering-review sandbox/recovery primitives generalized for a second concrete consumer, Project A generation store, Project C Blueprint/deviation policy, existing skill quarantine scanners, Python subprocess/venv, fixture MCP server, `gnothi_seauton`, pytest.

## Global Constraints

- Read design sections “Evolution Builder and Generation Store”, “Component
  Adapters”, “Quarantine and Build Isolation”, and “Gnothi Seauton
  Integration”.
- Depends on a valid unconsumed build grant from Project C.
- No stable installation edits, active/LKG pointer writes, global package
  install, privilege escalation, production credential, production endpoint,
  or arbitrary network after acquisition.
- Static validation precedes imports/execution.
- Every adapter implements one common contract and returns bounded typed
  records. It cannot write the ledger directly.
- A material deviation ends the build or returns it to Workshop; it is never
  silently accepted.
- A build failure may preserve bounded quarantine/evidence according to
  retention, but it must never expose a partial generation.

---

## File and Module Map

| Path | Responsibility |
|---|---|
| `hermes_cli/evolution/adapters/base.py` | Adapter protocol and common result records |
| `hermes_cli/evolution/adapters/skill.py` | Skill validation/discovery |
| `hermes_cli/evolution/adapters/script.py` | Script/extension-pack runtime and lock |
| `hermes_cli/evolution/adapters/plugin.py` | Local plugin manifest/import/registration |
| `hermes_cli/evolution/adapters/mcp.py` | MCP config/server discovery |
| `hermes_cli/evolution/quarantine.py` | Source/provenance capture and static gates |
| `hermes_cli/evolution/builder.py` | Host orchestration and grant consumption |
| `hermes_cli/evolution/build_authority.py` | Owner-only sandbox/evidence lifecycle |
| `hermes_cli/sandboxing/` | Generic hardened execution extracted from engineering review |
| `hermes_cli/evolution/candidate_gnothi.py` | Candidate-scoped organism build/diff |
| `tests/fixtures/autopoiesis/` | Local, network-free adapter fixtures |

## Common Adapter Contract

```python
class EvolutionAdapter(Protocol):
    component_class: ComponentClass

    def validate_blueprint(self, spec: ComponentSpec) -> None: ...
    def acquire(self, spec: ComponentSpec, context: AcquisitionContext) -> QuarantinedArtifact: ...
    def inspect(self, artifact: QuarantinedArtifact,
                context: SandboxContext) -> InspectionResult: ...
    def resolve_dependencies(self, artifact: QuarantinedArtifact,
                             context: SandboxContext) -> DependencyResolution: ...
    def verify(self, artifact: QuarantinedArtifact,
               context: SandboxContext) -> VerificationResult: ...
    def materialize(self, artifact: QuarantinedArtifact,
                    generation_stage: Path) -> AdapterResult: ...
    def describe_capabilities(self, result: AdapterResult) -> tuple[CapabilityFact, ...]: ...
```

Every method receives immutable records and an adapter-scoped directory. No
context object contains a pointer path, ledger connection, stable source root,
or production credential.

## Task 1: Common adapter records and conformance harness

**Model:** `gpt-5.6-terra`, reasoning `medium`

**Objective:** Freeze the contract and behavioral conformance suite before any
component-specific logic exists.

**Non-goals:** No sandbox or real adapter.

**Files:**
- Create: `hermes_cli/evolution/adapters/__init__.py`
- Create: `hermes_cli/evolution/adapters/base.py`
- Create: `tests/hermes_cli/evolution/adapters/__init__.py`
- Create: `tests/hermes_cli/evolution/adapters/conformance.py`
- Create: `tests/hermes_cli/evolution/adapters/test_base.py`

**Context pack:** Index `AdapterResult`; Project C Blueprint component schema
and deviation classifier; Project A path/digest validators.

- [ ] **Step 1: Write failing record validation tests**

Reject unknown class, duplicate logical ID, absolute/escaping path, missing
artifact/evidence digest, unbounded diagnostic, capability without evidence,
and mutable mapping/list fields inside frozen records.

- [ ] **Step 2: Write a reusable conformance function**

`assert_adapter_conforms(adapter, fixture, tmp_path)` calls methods in order,
proves inputs are not mutated, paths stay in scope, results serialize
canonically, repeat execution converges, and a failure cannot materialize.

- [ ] **Step 3: Run red tests**

Run: `scripts/run_tests.sh tests/hermes_cli/evolution/adapters/test_base.py -q`

- [ ] **Step 4: Implement frozen records and protocol**

Use tuples and read-only mappings at public boundaries. Convert exceptions to
typed `AdapterError(code, safe_summary)` without raw command/output/path.

- [ ] **Step 5: Run tests and commit**

Commit: `evolution-d: define peripheral adapter contract`

**Escalate if:** an adapter requires lifecycle authority or a result can omit
the digest of executable/config bytes.

## Task 2: Generalize hardened sandbox and create BuildAuthority

**Model:** `gpt-5.6-sol`, reasoning `high`

**Objective:** Reuse the engineering-review isolation/recovery design without
duplicating or weakening it, and keep candidate output non-authoritative.

**Non-goals:** No component acquisition or adapter implementation.

**Files:**
- Create: `hermes_cli/sandboxing/__init__.py`
- Create: `hermes_cli/sandboxing/policy.py`
- Create: `hermes_cli/sandboxing/runner.py`
- Create: `hermes_cli/sandboxing/private_artifacts.py`
- Modify: `hermes_cli/engineering_review/execution_policy.py`
- Modify: `hermes_cli/engineering_review/terminal_execution.py`
- Modify: `hermes_cli/engineering_review/runs.py`
- Modify: `hermes_cli/engineering_review/recovery.py`
- Create: `hermes_cli/evolution/build_authority.py`
- Create: `tests/hermes_cli/evolution/test_build_authority.py`
- Create: `tests/hermes_cli/evolution/test_sandbox_boundary.py`
- Modify: `tests/hermes_cli/engineering_review/test_execution_policy.py`
- Modify: `tests/hermes_cli/engineering_review/test_terminal_execution.py`
- Modify: `tests/hermes_cli/engineering_review/test_runs.py`
- Modify: `tests/hermes_cli/engineering_review/test_recovery.py`

**Context pack:** Current engineering-review authority, execution policy,
terminal execution, runs, recovery and all their tests; Blueprint isolation
policy.

**Interfaces:**

```python
@dataclass(frozen=True)
class SandboxPolicy:
    network: Literal["deny", "fixture_only"]
    readable_roots: tuple[Path, ...]
    writable_roots: tuple[Path, ...]
    environment_allowlist: tuple[str, ...]
    cpu_seconds: int
    wall_seconds: int
    memory_bytes: int
    disk_bytes: int
    process_limit: int
    output_bytes: int

class SandboxRunner:
    def run(self, request: SandboxRequest) -> SandboxResult: ...
    def terminate(self) -> None: ...

class BuildAuthority:
    @classmethod
    def create(
        cls,
        *,
        blueprint: EvolutionBlueprint,
        grant: AuthorizationGrant,
        workspace: Path,
        sandbox_policy: SandboxPolicy,
    ) -> "BuildAuthority": ...
    def run_untrusted(self, request: SandboxRequest) -> UntrustedEvidence: ...
    def verify_evidence(self, evidence: UntrustedEvidence) -> VerifiedEvidence: ...
    def close(self) -> None: ...
```

- [ ] **Step 1: Lock existing review behavior with regression tests**

Run the full engineering-review suite before edits and record the green
baseline. Add tests proving compatibility re-exports preserve current public
imports.

- [ ] **Step 2: Write failing Builder boundary tests**

Candidate subprocess sees only the workspace and fixture roots, sanitized
environment, deterministic locale/timezone, no provider/backend tokens, no
evolution DB/pointers, network denied, bounded output, and forced cleanup.

- [ ] **Step 3: Extract only generic primitives**

Move private path validation, sandbox policy/result records, sanitized
environment construction, runner, and registered resource cleanup. Leave
review-specific target selection, evidence semantics, and commands in
`engineering_review`.

- [ ] **Step 4: Make review modules compatibility adapters**

They import/re-export the generic implementations and retain their exact
existing behavior/tests. Do not rewrite review logic opportunistically.

- [ ] **Step 5: Implement BuildAuthority**

The authority owns the build grant capability and executable request. There is
no RPC to issue grants, append arbitrary evidence, choose a different command,
change policy, publish a generation, or write pointers.

- [ ] **Step 6: Test startup/cleanup failures**

Inject failures after workspace creation, sandbox create, process start,
evidence write, and cleanup. Registered resources are removed or marked for
deterministic recovery; authority capability is always revoked.

- [ ] **Step 7: Run both suites and commit**

```bash
scripts/run_tests.sh tests/hermes_cli/engineering_review -q
scripts/run_tests.sh tests/hermes_cli/evolution/test_build_authority.py tests/hermes_cli/evolution/test_sandbox_boundary.py -q
```

Commit: `sandbox: share hardened execution with evolution builder`

**Escalate if:** review tests regress, candidate chooses its command/policy, or
the host trusts a candidate-authored success flag.

## Task 3: Skill adapter

**Model:** `gpt-5.6-terra`, reasoning `high`

**Objective:** Quarantine, inspect, discover, and invoke an approved skill
through the real scanner in an isolated home.

**Non-goals:** No global/local user skill installation.

**Files:**
- Create: `hermes_cli/evolution/adapters/skill.py`
- Create: `tests/hermes_cli/evolution/adapters/test_skill.py`
- Create: `tests/fixtures/autopoiesis/skill/valid/SKILL.md`
- Create: `tests/fixtures/autopoiesis/skill/valid/scripts/probe.py`
- Create: `tests/fixtures/autopoiesis/skill/escape/SKILL.md`

**Context pack:** Adapter contract; `tools/skills_hub.py` quarantine/install
functions; `tools/skills_guard.py`; skill scanner and skill size tests.

- [ ] **Step 1: Add conformance and malicious fixture tests**

Cover missing/incomplete instructions, frontmatter errors, referenced resource
escape/symlink, oversized file, forbidden binary, hidden executable, absolute
helper path, and digest change after inspection.

- [ ] **Step 2: Run red tests**

Run: `scripts/run_tests.sh tests/hermes_cli/evolution/adapters/test_skill.py -q`

- [ ] **Step 3: Implement static quarantine using existing scanners**

Call the existing scan/quarantine primitives directly; do not shell out to an
installer and do not copy into `~/.hermes/skills`.

- [ ] **Step 4: Implement real isolated discovery/invocation**

Build a temporary Hades home containing only the fixture as an overlay root,
run the real skill scanner, load the complete `SKILL.md`, and execute only the
Blueprint-declared helper through `SandboxRunner`.

- [ ] **Step 5: Run conformance/regression tests and commit**

Commit: `evolution-d: validate quarantined skill overlays`

**Escalate if:** discovery needs config mutation, a resource escapes the skill
root, or validation executes before static inspection.

## Task 4: Script and extension-pack adapter

**Model:** `gpt-5.6-terra`, reasoning `high`

**Objective:** Produce a locked, isolated, bounded script component with an
explicit runtime and invocation contract.

**Non-goals:** No shell profile edit, global interpreter/package install, or
arbitrary entry point.

**Files:**
- Create: `hermes_cli/evolution/adapters/script.py`
- Create: `tests/hermes_cli/evolution/adapters/test_script.py`
- Create: `tests/fixtures/autopoiesis/script/valid/extension.json`
- Create: `tests/fixtures/autopoiesis/script/valid/main.py`
- Create: `tests/fixtures/autopoiesis/script/forbidden/main.py`

**Context pack:** Adapter contract; sandbox policy; Python venv/runtime helpers
already in repository; Blueprint dependency constraints.

**Extension manifest v1:**

```json
{
  "schema_version": 1,
  "id": "fixture-normalizer",
  "runtime": "python3",
  "entry_point": "main.py",
  "invoked_by": {"component_id": "fixture-normalizer-skill", "kind": "skill"},
  "arguments_schema": {"type": "object", "additionalProperties": false},
  "dependency_lock": "requirements.lock",
  "network": "deny"
}
```

- [ ] **Step 1: Write failing manifest/runtime tests**

Reject implicit entry point/runtime, shell command strings, global dependency,
unlocked dependency, interpreter outside allowed set, undeclared environment
variable, subprocess/network attempt, output overflow, timeout, and write
escape. Reject an orphan script: `invoked_by` must identify an approved skill
or local-plugin component in the same Blueprint, and that component's verified
instructions/registration must reference the exact script logical ID.

- [ ] **Step 2: Run red tests**

Run: `scripts/run_tests.sh tests/hermes_cli/evolution/adapters/test_script.py -q`

- [ ] **Step 3: Implement dependency resolution**

Resolve only approved families/constraints into an isolated environment,
record exact package/version/artifact digest, and emit a deterministic lock.
Tests use local fixture artifacts so no public network is needed.

- [ ] **Step 4: Implement real invocation verification**

Invoke an argv array, never `shell=True`; feed canonical JSON on stdin and
require one bounded canonical JSON result. Enforce all SandboxPolicy ceilings.

- [ ] **Step 5: Run conformance tests and commit**

Commit: `evolution-d: lock isolated script extensions`

**Escalate if:** lock generation depends on mutable indexes without artifact
digests or an invocation can inherit production environment state.

## Task 5: Local-plugin adapter

**Model:** `gpt-5.6-terra`, reasoning `high`

**Objective:** Validate a standalone local plugin through its real
`register(ctx)` interface without touching the process-global plugin/tool
registries.

**Non-goals:** No platform, model-provider, backend, or exclusive memory
plugin in the MVP.

**Files:**
- Create: `hermes_cli/evolution/adapters/plugin.py`
- Create: `tests/hermes_cli/evolution/adapters/test_plugin.py`
- Create: `tests/fixtures/autopoiesis/plugin/valid/plugin.yaml`
- Create: `tests/fixtures/autopoiesis/plugin/valid/__init__.py`
- Create: `tests/fixtures/autopoiesis/plugin/escape/__init__.py`

**Context pack:** `PluginManifest`, `PluginContext`, `_load_directory_module`,
plugin tests; adapter/sandbox contracts.

- [ ] **Step 1: Write failing manifest/kind tests**

Accept only `kind: standalone`, schema version, supported API version, unique
name/version, declared tools/hooks/skills, author/license/source, and no
override of a built-in name.

- [ ] **Step 2: Write failing subprocess registration tests**

Use a recorder context with the supported `PluginContext` methods. Reject
undeclared registrations, platform/global config access, import-time write,
network, child process, core-file import mutation, and cleanup failure.

- [ ] **Step 3: Run red tests**

Run: `scripts/run_tests.sh tests/hermes_cli/evolution/adapters/test_plugin.py -q`

- [ ] **Step 4: Implement static import graph inspection**

Reject relative escapes, dynamic path injection, and manifest/module mismatch
before sandboxed import. Do not attempt a complete malware proof; rely on the
sandbox for runtime behavior.

- [ ] **Step 5: Implement isolated registration and unload probe**

Import under a generation-qualified module namespace in a subprocess, call
`register(recorder_ctx)`, compare actual declarations with the manifest, invoke
one fixture tool, run session-end cleanup, and terminate the subprocess.

- [ ] **Step 6: Run plugin regressions and commit**

Run adapter tests plus `tests/hermes_cli/test_plugins.py`.

Commit: `evolution-d: verify isolated local plugins`

**Escalate if:** validation mutates global registries, permits overrides, or a
plugin survives its sandbox lifecycle.

## Task 6: MCP-configuration adapter

**Model:** `gpt-5.6-terra`, reasoning `high`

**Objective:** Validate approved MCP configuration and real tool discovery
against a side-effect-denied fixture server.

**Non-goals:** No OAuth flow, production credential, unapproved remote URL, or
global `config.yaml` change.

**Files:**
- Create: `hermes_cli/evolution/adapters/mcp.py`
- Create: `tests/hermes_cli/evolution/adapters/test_mcp.py`
- Create: `tests/fixtures/autopoiesis/mcp/fixture_server.py`
- Create: `tests/fixtures/autopoiesis/mcp/server.json`

**Context pack:** `tools/mcp_tool.py` config loader/register/discovery;
MCP security/capability-gating tests; adapter policy.

- [ ] **Step 1: Write failing config tests**

Reject undeclared command/URL, relative executable, shell metacharacter
command string, environment key outside allowlist, secret value rather than
reference name, redirect outside allowed domain, unsupported transport,
undeclared tool, duplicate exposed tool, and startup/resource limit breach.

- [ ] **Step 2: Run red tests**

Run: `scripts/run_tests.sh tests/hermes_cli/evolution/adapters/test_mcp.py -q`

- [ ] **Step 3: Implement canonical config projection**

Store argv arrays or validated HTTPS endpoints, transport, tool allow/deny
lists, credential reference names, capability declarations, and timeouts. No
environment secret values enter the generation.

- [ ] **Step 4: Implement real fixture discovery**

Start the fixture server through `SandboxRunner`, perform real MCP initialize
and tools/list, compare schemas/names with declarations, invoke one
side-effect-free fixture tool, and cleanly shut down.

- [ ] **Step 5: Run MCP regressions and commit**

Run adapter tests plus focused MCP capability/security tests.

Commit: `evolution-d: validate generation-scoped mcp configs`

**Escalate if:** adapter needs production credentials/network or discovery
registers tools globally.

## Task 7: Builder orchestration, acquisition, and immutable publication

**Model:** `gpt-5.6-terra`, reasoning `high`

**Objective:** Orchestrate the approved adapter set from grant consumption to
one verified quarantined generation.

**Non-goals:** No canary, promotion report, or pointer switch.

**Files:**
- Create: `hermes_cli/evolution/quarantine.py`
- Create: `hermes_cli/evolution/builder.py`
- Modify: `hermes_cli/evolution/command.py`
- Modify: `hermes_cli/subcommands/evolution.py`
- Create: `tests/hermes_cli/evolution/test_quarantine.py`
- Create: `tests/hermes_cli/evolution/test_builder.py`
- Create: `tests/hermes_cli/evolution/test_builder_recovery.py`

**Context pack:** BuildAuthority; all adapters; Project A store/ledger; Project
C grant/deviation API.

**Interfaces:**

```python
def build_candidate(*, blueprint_digest: str, grant_id: str,
                    dry_run: bool = False) -> BuildResult: ...
def reconcile_build(candidate_id: str) -> BuildRecoveryResult: ...
```

- [ ] **Step 1: Write failing precondition tests**

Wrong/expired/consumed grant, changed Blueprint, unlisted adapter/source,
incompatible stable base, existing active build, or unsafe workspace stops
before acquisition.

- [ ] **Step 2: Write failing acquisition/provenance tests**

Record approved source, redirects, author, license, version, retrieval time,
actual digest, and grant ID. Digest mismatch, source drift, or missing license
fails before execution.

- [ ] **Step 3: Write failing orchestration/recovery tests**

Inject failure at grant consumption, workspace create, each adapter stage,
manifest assembly, generation rename, reopen verification, and ledger outcome.
Only a disposable workspace or fully verified generation may remain.

- [ ] **Step 4: Run red tests**

Run the three new test files.

- [ ] **Step 5: Implement ordered host orchestration**

Under lifecycle lock: validate/consume grant and transition to `building`;
create private workspace; acquire; validate/inspect/resolve/verify each
component; classify every observation against Blueprint; assemble stage;
publish; reverify; append generation/components and transition to
`quarantined`.

- [ ] **Step 6: Implement dry run**

Validate grant/request/Blueprint and render planned sources, adapters,
resources, and paths without consuming the grant, creating a workspace,
networking, or executing code.

- [ ] **Step 7: Add `build` and inspection commands**

`evolution build --blueprint <digest> --grant <id> [--dry-run]`,
`show generation`, and workspace cleanup/reconcile. There is no activate flag.

- [ ] **Step 8: Run tests and commit**

Commit: `evolution-d: materialize approved overlay generations`

**Escalate if:** orchestration passes a ledger handle to an adapter, executes
before provenance/digest checks, or retains a partial published generation.

## Task 8: Candidate-scoped `gnothi_seauton` revision and semantic diff

**Model:** `gpt-5.6-terra`, reasoning `high`

**Objective:** Build an organism revision from the candidate's actual isolated
discovery results and compare it with its parent without scope leakage.

**Non-goals:** No stable promotion or global plugin/MCP registration.

**Files:**
- Create: `hermes_cli/evolution/candidate_gnothi.py`
- Modify: `hermes_cli/gnothi/collectors/base.py`
- Modify: `hermes_cli/gnothi/collectors/capabilities.py`
- Modify: `hermes_cli/gnothi/builder.py`
- Modify: `hermes_cli/gnothi/query.py`
- Create: `tests/hermes_cli/evolution/test_candidate_gnothi.py`
- Modify: `tests/hermes_cli/test_gnothi_builder.py`
- Modify: `tests/hermes_cli/test_gnothi_contract.py`

**Context pack:** Existing Gnothi generation scopes/collectors; adapter
capability facts; generation manifest.

**Interfaces:**

```python
@dataclass(frozen=True)
class CapabilitySnapshot:
    generation_id: str
    generation_scope: Literal["candidate"]
    tools: tuple[Mapping[str, object], ...]
    skills: tuple[Mapping[str, object], ...]
    plugins: tuple[Mapping[str, object], ...]
    mcp_servers: tuple[Mapping[str, object], ...]
    evidence_digests: tuple[str, ...]

def build_candidate_revision(generation_id: str) -> CandidateOrganismResult: ...
```

- [ ] **Step 1: Write failing scope tests**

Candidate nodes are `candidate`, parent nodes remain `stable`/`historical`,
and no edge crosses scopes. Candidate facts cannot verify stable capability.

- [ ] **Step 2: Write failing real-discovery tests**

Snapshot must come from adapter/sandbox discovery output verified against
generation bytes, not merely Blueprint declarations.

- [ ] **Step 3: Run red tests**

Run candidate and Gnothi tests.

- [ ] **Step 4: Add explicit collector input**

Extend `CollectorContext` with an optional frozen capability snapshot. Default
behavior remains unchanged. When present, CapabilityCollector reads that
snapshot instead of process-global registries and sets generation ID/scope
explicitly.

- [ ] **Step 5: Build and store candidate revision**

Use the immutable generation ID, retain evidence digests, produce expected vs
actual semantic diff, and store under candidate scope without changing the
current stable revision pointer.

- [ ] **Step 6: Run tests and commit**

Commit: `evolution-d: describe candidate organism without scope leakage`

**Escalate if:** candidate collection imports code in the host or modifies the
current stable organism revision.

## Task 9: Builder isolation and A3 security gate

**Model:** `gpt-5.6-sol`, reasoning `high`

**Objective:** Independently validate the entire build boundary and all four
real adapters before Project E receives a generation.

**Non-goals:** No canary or activation.

**Files:**
- Create: `tests/integration/test_autopoiesis_builder.py`
- Create: `tests/integration/test_autopoiesis_adapter_matrix.py`
- Create: `docs/autopoiesis/builder-v1.md`

No production files are planned in this verification task. A reproduced defect
must become a focused failing regression and a separate repair task before the
gate is rerun.

**Context pack:** All Project D diffs; engineering-review regression output;
design Builder/isolation/privacy criteria 6–7, 10, 17, 19.

- [ ] **Step 1: Run each real adapter end to end**

Use local fixtures and one approved multi-component Blueprint. Verify exact
bytes/digests, real discovery/invocation, clean termination, and one immutable
generation.

- [ ] **Step 2: Run the adversarial matrix**

For each adapter attempt stable/pointer/ledger/config/memory writes, secret
read, network, child process, symlink/path escape, output flood, timeout, and
self-reported success after failure. Every attempt is denied or yields
`build_failed`.

- [ ] **Step 3: Prove no authority leakage**

Inspect process descriptors, environment, mounts, RPC methods, and adapter
contexts. Candidate code must not possess a ledger capability, pointer path,
promotion API, production credential, or unrestricted host executor.

- [ ] **Step 4: Prove engineering review remains intact**

Run its complete suite and compare public behavior with the Task 2 baseline.

- [ ] **Step 5: Run the project matrix**

```bash
scripts/run_tests.sh tests/hermes_cli/evolution -q
scripts/run_tests.sh tests/integration/test_autopoiesis_foundation.py tests/integration/test_autopoiesis_observer.py tests/integration/test_autopoiesis_workshop.py tests/integration/test_autopoiesis_builder.py tests/integration/test_autopoiesis_adapter_matrix.py -q
scripts/run_tests.sh tests/hermes_cli/engineering_review -q
scripts/run_tests.sh tests/hermes_cli/test_gnothi_*.py -q
```

- [ ] **Step 6: Document and commit**

Commit: `evolution-d: verify quarantined generation construction`

Handoff one quarantined generation ID, manifest digest, candidate organism
revision, and bounded evidence digests. Confirm both pointers still identify
the prior baseline/active generation.

**Escalate if:** any adapter passes only with a mock loader, candidate code
reaches host authority, or engineering-review isolation weakens.
