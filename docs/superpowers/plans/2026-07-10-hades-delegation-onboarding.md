# Hades Delegation Onboarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add guided model routing, real reviewer semantics, validated orchestrator contracts, adaptive capacity, hierarchical evidence, and installable Hades skill guidance.

**Architecture:** Pure modules own contracts, capacity, evidence, and recommendation logic. Thin CLI integration reuses the authenticated model inventory and atomically updates the active profile. `delegate_task` consumes those modules without adding a permanently visible core tool.

**Tech Stack:** Python 3.11+, argparse, dataclasses, httpx-independent local logic, SQLite/config YAML helpers, pytest.

## Global Constraints

- Preserve the initial system prompt and tool schema for the lifetime of an existing conversation.
- Do not put API keys, tokens, passwords, or other credentials in delegation profiles.
- Existing `delegation.model` and `delegation.provider` behavior remains valid when no role route applies.
- Existing valid routing is never changed automatically.
- Normal review belongs to the parent; `reviewer` is an explicit escalation role.
- `capacity_mode: adaptive` still obeys user-selected hard ceilings.
- No child is created before its role, contract, budget, and capacity checks pass.
- Evidence contains bounded facts and references, never hidden reasoning or full transcripts.

## File map

- Create `tools/delegation_contract.py`: parse and validate orchestrator task contracts.
- Create `tools/delegation_capacity.py`: collect capacity inputs and return a deterministic decision.
- Create `tools/delegation_evidence.py`: create, validate, and invalidate evidence packets.
- Create `hermes_cli/delegation_onboarding.py`: inventory normalization, recommendations, and config patch construction.
- Create `hermes_cli/hades_delegation_cmd.py`: `hades delegation setup|configure` parser and wizard.
- Modify `tools/delegate_tool.py`: reviewer behavior, contract/capacity gates, and evidence integration.
- Modify `tools/delegation_routing.py`: role profile compatibility and onboarding-facing serialization.
- Modify `hermes_cli/main.py`: register the new command without growing interactive CLI logic.
- Modify `skills/software-development/hierarchical-development/SKILL.md`: onboarding and responsibility protocol.

---

### Task 1: Make `reviewer` a real non-delegating runtime role

**Files:**
- Modify: `tools/delegate_tool.py` (`_normalize_role`, `_build_child_system_prompt`, `_build_child_agent`, dynamic schema)
- Test: `tests/tools/test_delegate.py`
- Test: `tests/tools/test_delegation_routing.py`

**Interfaces:**
- Consumes: `resolve_role_profile(routing: DelegationRouting, role: str) -> DelegationProfile | None`.
- Produces: normalized roles `leaf | orchestrator | reviewer`; only an effective orchestrator receives the delegation toolset.

- [ ] **Step 1: Write failing role tests**

```python
def test_reviewer_role_is_preserved_but_cannot_delegate(monkeypatch):
    monkeypatch.setattr("tools.delegate_tool._load_config", lambda: {"max_spawn_depth": 3})
    assert _normalize_role("reviewer") == "reviewer"
    prompt = _build_child_system_prompt(
        "Review evidence", role="reviewer", max_spawn_depth=3, child_depth=1
    )
    assert "independent review" in prompt.lower()
    assert "delegate_task" not in prompt

def test_schema_advertises_all_three_roles():
    role = DELEGATE_TASK_SCHEMA["parameters"]["properties"]["role"]
    assert role["enum"] == ["leaf", "orchestrator", "reviewer"]
```

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `.venv/bin/python -m pytest tests/tools/test_delegate.py tests/tools/test_delegation_routing.py -q`

Expected: failures because `reviewer` is coerced to `leaf` and absent from the schema.

- [ ] **Step 3: Implement the role semantics**

```python
DELEGATION_ROLES = frozenset({"leaf", "orchestrator", "reviewer"})

def _normalize_role(value: Optional[str]) -> str:
    role = str(value or "leaf").strip().lower()
    if role in DELEGATION_ROLES:
        return role
    logger.warning("Unknown delegate_task role=%r, coercing to 'leaf'", value)
    return "leaf"

if role == "orchestrator" and orchestrator_ok:
    effective_role = "orchestrator"
elif role == "reviewer":
    effective_role = "reviewer"
else:
    effective_role = "leaf"
```

Add a reviewer prompt block requiring findings-first output, scope checks, evidence checks, regression risk, and a bounded pass/fail conclusion. Keep `_strip_blocked_tools` unchanged so reviewer and leaf cannot delegate.

- [ ] **Step 4: Run the focused tests and confirm GREEN**

Run: `.venv/bin/python -m pytest tests/tools/test_delegate.py tests/tools/test_delegation_routing.py -q`

Expected: all focused tests pass.

- [ ] **Step 5: Commit**

```bash
git add tools/delegate_tool.py tests/tools/test_delegate.py tests/tools/test_delegation_routing.py
git commit -m "feat(hades): add reviewer delegation role"
```

---

### Task 2: Require structured contracts before orchestrator spawn

**Files:**
- Create: `tools/delegation_contract.py`
- Create: `tests/tools/test_delegation_contract.py`
- Modify: `tools/delegate_tool.py`
- Test: `tests/tools/test_delegate.py`

**Interfaces:**
- Produces: `OrchestratorTaskContract`, `parse_orchestrator_contract(raw)`, and `contract_prompt_block(contract)`.
- Consumes: `task_contract` from single-task and batch `delegate_task` arguments.

- [ ] **Step 1: Write failing parser and pre-spawn tests**

```python
VALID = {
    "objective": "Implement routing",
    "deliverable": "Tested CLI command",
    "in_scope": ["hermes_cli/delegation_onboarding.py"],
    "out_of_scope": ["backend deployment"],
    "workspace": ".",
    "write_scope": ["hermes_cli/**", "tests/**"],
    "input_evidence": ["spec:delegation-onboarding"],
    "dependencies": [],
    "acceptance_criteria": ["focused tests pass"],
    "required_verification": ["pytest tests/hermes_cli/test_delegation_onboarding.py -q"],
    "return_schema": ["child_plan", "evidence", "risks", "escalations"],
}

def test_contract_requires_every_nonempty_semantic_field():
    required_nonempty = set(VALID) - {"dependencies"}
    for key in required_nonempty:
        broken = {**VALID, key: [] if isinstance(VALID[key], list) else ""}
        with pytest.raises(ValueError, match=key):
            parse_orchestrator_contract(broken)

def test_contract_allows_explicit_empty_dependencies():
    assert parse_orchestrator_contract(VALID).dependencies == ()

def test_invalid_orchestrator_contract_does_not_build_child(monkeypatch):
    build = Mock()
    monkeypatch.setattr("tools.delegate_tool._build_child_agent", build)
    result = json.loads(delegate_task(goal="Plan", role="orchestrator", parent_agent=_make_parent()))
    assert result["status"] == "error"
    build.assert_not_called()
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `.venv/bin/python -m pytest tests/tools/test_delegation_contract.py tests/tools/test_delegate.py -q`

Expected: import/schema failures because the contract module and argument do not exist.

- [ ] **Step 3: Implement the immutable contract**

```python
@dataclass(frozen=True)
class OrchestratorTaskContract:
    objective: str
    deliverable: str
    in_scope: tuple[str, ...]
    out_of_scope: tuple[str, ...]
    workspace: str
    write_scope: tuple[str, ...]
    input_evidence: tuple[str, ...]
    dependencies: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    required_verification: tuple[str, ...]
    return_schema: tuple[str, ...]

def parse_orchestrator_contract(raw: Mapping[str, Any]) -> OrchestratorTaskContract:
    if not isinstance(raw, Mapping):
        raise ValueError("task_contract is required for orchestrator role")
    return OrchestratorTaskContract(
        objective=_required_text(raw, "objective"),
        deliverable=_required_text(raw, "deliverable"),
        in_scope=_required_list(raw, "in_scope"),
        out_of_scope=_required_list(raw, "out_of_scope"),
        workspace=_required_text(raw, "workspace"),
        write_scope=_required_list(raw, "write_scope"),
        input_evidence=_required_list(raw, "input_evidence"),
        dependencies=_required_list(raw, "dependencies", allow_empty=True),
        acceptance_criteria=_required_list(raw, "acceptance_criteria"),
        required_verification=_required_list(raw, "required_verification"),
        return_schema=_required_list(raw, "return_schema"),
    )
```

Add `task_contract` to the top-level and per-task schemas. Parse it immediately after role normalization and before budget reservation or `_build_child_agent`. Append `contract_prompt_block()` only for orchestrators.

- [ ] **Step 4: Run focused tests**

Run: `.venv/bin/python -m pytest tests/tools/test_delegation_contract.py tests/tools/test_delegate.py -q`

Expected: all tests pass and the mock confirms zero child construction on invalid input.

- [ ] **Step 5: Commit**

```bash
git add tools/delegation_contract.py tools/delegate_tool.py tests/tools/test_delegation_contract.py tests/tools/test_delegate.py
git commit -m "feat(hades): validate orchestrator task contracts"
```

---

### Task 3: Add adaptive capacity decisions without removing hard ceilings

**Files:**
- Create: `tools/delegation_capacity.py`
- Create: `tests/tools/test_delegation_capacity.py`
- Modify: `tools/delegation_budget.py`
- Modify: `tools/delegate_tool.py`

**Interfaces:**
- Produces: `CapacitySnapshot`, `CapacityRequest`, `CapacityDecision`, `probe_capacity()`, and `decide_capacity()`.
- Consumes: role, requested iterations, depth, current budget snapshot, active process/agent counts, provider availability, and configured ceilings.

- [ ] **Step 1: Write table-driven failing tests**

```python
@pytest.mark.parametrize((
    "snapshot, expected"
), [
    (snapshot(memory_pressure=0.95), "queue"),
    (snapshot(provider_available=False), "queue"),
    (snapshot(depth=3, max_depth=3), "degrade_to_leaf"),
    (snapshot(iterations_remaining=0), "replan"),
    (snapshot(), "allow"),
])
def test_capacity_decisions(snapshot, expected):
    assert decide_capacity(snapshot).action == expected
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `.venv/bin/python -m pytest tests/tools/test_delegation_capacity.py -q`

Expected: module import fails.

- [ ] **Step 3: Implement deterministic policy and safe probes**

```python
@dataclass(frozen=True)
class CapacityDecision:
    action: Literal["allow", "queue", "degrade_to_leaf", "replan"]
    reason: str

def decide_capacity(s: CapacitySnapshot) -> CapacityDecision:
    if s.iterations_remaining <= 0 or s.children_remaining <= 0:
        return CapacityDecision("replan", "delegation tree budget exhausted")
    if s.depth >= s.max_depth:
        return CapacityDecision("degrade_to_leaf", "spawn depth ceiling reached")
    if s.provider_available is False or s.memory_pressure >= 0.90:
        return CapacityDecision("queue", "capacity temporarily unavailable")
    return CapacityDecision("allow", "capacity available")
```

`probe_capacity()` must use stdlib probes first and optional `psutil` only when already installed. Unknown metrics are `None`, never treated as permission to exceed configured ceilings.

- [ ] **Step 4: Integrate preflight before reservation/spawn**

Map `queue` and `replan` to explicit tool errors; map `degrade_to_leaf` only when the requested role is orchestrator and then re-run routing for `leaf`. Do not commit a tree reservation until child construction succeeds.

Run: `.venv/bin/python -m pytest tests/tools/test_delegation_capacity.py tests/tools/test_delegation_budget.py tests/tools/test_delegate.py -q`

Expected: all focused tests pass.

- [ ] **Step 5: Commit**

```bash
git add tools/delegation_capacity.py tools/delegation_budget.py tools/delegate_tool.py tests/tools/test_delegation_capacity.py tests/tools/test_delegation_budget.py tests/tools/test_delegate.py
git commit -m "feat(hades): add adaptive delegation capacity"
```

---

### Task 4: Produce reusable and invalidatable evidence packets

**Files:**
- Create: `tools/delegation_evidence.py`
- Create: `tests/tools/test_delegation_evidence.py`
- Modify: `tools/delegate_tool.py` (`_run_single_child` and result aggregation)

**Interfaces:**
- Produces: `EvidencePacket`, `build_evidence_packet()`, `validate_evidence_packet()`, and `evidence_is_stale()`.
- Consumes: contract version/hash, Git state captured before/after, child tool trace, structured verification records, and bounded final summary.

- [ ] **Step 1: Write failing evidence tests**

```python
def test_changed_diff_invalidates_packet():
    packet = build_evidence_packet(contract_hash="c1", base_commit="a" * 40, diff_hash="d1", covered_files=("a.py",), verification=())
    assert not evidence_is_stale(packet, contract_hash="c1", base_commit="a" * 40, diff_hash="d1", dependency_hashes=())
    assert evidence_is_stale(packet, contract_hash="c1", base_commit="a" * 40, diff_hash="d2", dependency_hashes=())

def test_packet_serialization_has_no_messages_or_reasoning():
    payload = packet.to_dict()
    assert "messages" not in payload
    assert "reasoning" not in payload
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `.venv/bin/python -m pytest tests/tools/test_delegation_evidence.py -q`

Expected: module import fails.

- [ ] **Step 3: Implement the packet and hash helpers**

```python
@dataclass(frozen=True)
class EvidencePacket:
    schema: str
    contract_hash: str
    base_commit: str
    result_ref: str | None
    diff_hash: str
    covered_files: tuple[str, ...]
    verification: tuple[dict[str, Any], ...]
    conclusion: str
    dependency_hashes: tuple[str, ...]
    residual_risks: tuple[str, ...]
```

Use canonical JSON plus SHA-256 for hashes. Truncate user/model summaries to the documented bound and reject secrets with the existing redaction helpers.

- [ ] **Step 4: Integrate child evidence without serializing trajectories**

`_run_single_child` must build the packet from runtime facts and an optional structured `evidence` object returned by the child. It may use `tool_trace` to corroborate verification, but must not copy `messages`. Parent aggregation returns `evidence_packet` beside `summary`.

Run: `.venv/bin/python -m pytest tests/tools/test_delegation_evidence.py tests/tools/test_delegate.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add tools/delegation_evidence.py tools/delegate_tool.py tests/tools/test_delegation_evidence.py tests/tools/test_delegate.py
git commit -m "feat(hades): add delegation evidence packets"
```

---

### Task 5: Build deterministic model recommendations from authenticated inventory

**Files:**
- Create: `hermes_cli/delegation_onboarding.py`
- Create: `tests/hermes_cli/test_delegation_onboarding.py`
- Modify: `tools/delegation_routing.py`

**Interfaces:**
- Consumes: `build_models_payload(load_picker_context(), pricing=True, capabilities=True)`.
- Produces: `ConfiguredModel`, `DelegationRecommendation`, `configured_models()`, `recommend_role_models()`, `build_delegation_patch()`.

- [ ] **Step 1: Write failing recommendation tests**

```python
def test_recommendations_use_only_authenticated_rows():
    payload = {"providers": [
        {"slug": "openrouter", "authenticated": True, "models": ["strong", "cheap"], "capabilities": {"strong": {"reasoning": True}, "cheap": {"reasoning": False}}},
        {"slug": "other", "authenticated": False, "models": ["forbidden"]},
    ], "model": "strong", "provider": "openrouter"}
    result = recommend_role_models(normalize_inventory(payload))
    assert {x.model for x in result.values()} <= {"strong", "cheap"}
    assert result["orchestrator"].model == "strong"
    assert result["reviewer"].model == "strong"

def test_single_model_is_explicitly_reused_for_all_roles():
    result = recommend_role_models([configured("p", "only")])
    assert {entry.model for entry in result.values()} == {"only"}
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_delegation_onboarding.py -q`

Expected: module import fails.

- [ ] **Step 3: Implement normalization and stable scoring**

```python
ROLE_ORDER = ("orchestrator", "leaf", "reviewer")

def recommend_role_models(models: Sequence[ConfiguredModel]) -> dict[str, DelegationRecommendation]:
    if not models:
        return {}
    strongest = max(models, key=lambda m: (m.reasoning, m.context_length, -m.input_cost, m.provider, m.model))
    cheapest = min(models, key=lambda m: (m.input_cost + m.output_cost, not m.fast, m.provider, m.model))
    return {
        "orchestrator": recommendation(strongest, "strongest agentic reasoning"),
        "leaf": recommendation(cheapest, "lowest-cost compatible worker"),
        "reviewer": recommendation(strongest, "strongest verification reasoning"),
    }
```

Unknown pricing sorts after known pricing for the leaf recommendation. Every recommendation includes a human-readable reason and confidence; it never claims unavailable metadata.

- [ ] **Step 4: Build the exact config patch and validate it through the production parser**

`build_delegation_patch()` returns `profiles`, `role_routes`, `capacity_mode`, `max_spawn_depth`, `max_concurrent_children`, and `max_async_children`. Pass the result into `load_delegation_routing({"delegation": patch})` before returning it.

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_delegation_onboarding.py tests/tools/test_delegation_routing.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add hermes_cli/delegation_onboarding.py tools/delegation_routing.py tests/hermes_cli/test_delegation_onboarding.py tests/tools/test_delegation_routing.py
git commit -m "feat(hades): recommend delegated role models"
```

---

### Task 6: Add `hades delegation setup|configure` with atomic persistence

**Files:**
- Create: `hermes_cli/hades_delegation_cmd.py`
- Create: `tests/hermes_cli/test_hades_delegation_cmd.py`
- Modify: `hermes_cli/main.py`
- Modify: `utils.py` only if a whole-subtree round-trip update helper is missing

**Interfaces:**
- Produces: `build_parser(subparsers, cmd_delegation)`, `delegation_command(args)`, and `run_delegation_wizard(mode: Literal["setup", "configure"], *, inventory_loader: Callable[[], list[ConfiguredModel]], model_setup: Callable[[], bool], prompt: PromptIO, config_path: Path | None = None) -> WizardResult`.
- Consumes: `cmd_model`, inventory/recommendation functions, active profile config path, and `atomic_roundtrip_yaml_update`.

- [ ] **Step 1: Write failing parser and no-overwrite tests**

```python
def test_setup_refuses_to_replace_valid_routing(tmp_path, monkeypatch):
    write_config(tmp_path, VALID_ROUTING)
    result = run_delegation_wizard("setup", prompt=failing_prompt)
    assert result.code == 2
    assert result.next_command == "hades delegation configure"
    assert read_config(tmp_path) == VALID_ROUTING

def test_empty_inventory_runs_model_setup_then_resumes():
    calls = []
    result = run_delegation_wizard(
        "setup",
        inventory_loader=inventory_sequence([], [configured("openrouter", "m")]),
        model_setup=lambda: calls.append("model") or True,
        prompt=accept_defaults,
    )
    assert calls == ["model"]
    assert result.code == 0
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_delegation_cmd.py -q`

Expected: command module import fails.

- [ ] **Step 3: Implement the parser and injected wizard**

```python
def build_parser(subparsers, *, cmd_delegation):
    parser = subparsers.add_parser("delegation", help="Configure Hades subagent routing")
    sub = parser.add_subparsers(dest="delegation_action", required=True)
    for action in ("setup", "configure"):
        child = sub.add_parser(action)
        child.set_defaults(func=cmd_delegation)
```

`setup` detects valid routing and exits without prompting. `configure` loads current choices as defaults. If inventory is empty, call the existing model setup and reload inventory. Show role/model reasons, capacity ceilings, and the final YAML-equivalent mapping before a single confirmation.

- [ ] **Step 4: Persist only after confirmation and register in `main()`**

Use `atomic_roundtrip_yaml_update(config_path, "delegation", patch)`, preserve symlinks/comments, and chmod the active config to `0600` where supported. Add the parser registration beside `org` and a thin `cmd_delegation` wrapper.

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_delegation_cmd.py tests/test_atomic_replace_symlinks.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add hermes_cli/hades_delegation_cmd.py hermes_cli/main.py tests/hermes_cli/test_hades_delegation_cmd.py utils.py
git commit -m "feat(hades): add delegation onboarding wizard"
```

---

### Task 7: Update the bundled skill and verify installation primitives

**Files:**
- Modify: `skills/software-development/hierarchical-development/SKILL.md`
- Modify: `docs/hades/org-run-operations.md`
- Create: `tests/skills/test_hierarchical_development_skill.py`

**Interfaces:**
- Consumes: `hades delegation setup`, `hades delegation configure`, reviewer escalation, evidence packets, and the distributed-orchestration plan.
- Produces: discoverable skill instructions with frontmatter description no longer than 60 characters.

- [ ] **Step 1: Write failing skill contract tests**

```python
def test_skill_frontmatter_and_onboarding_contract():
    text = SKILL.read_text(encoding="utf-8")
    description = frontmatter(text)["description"]
    assert len(description) <= 60
    assert "hades delegation setup" in text
    assert "hades delegation configure" in text
    assert "task contract" in text.lower()
    assert "parent" in text.lower() and "evidence" in text.lower()
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `.venv/bin/python -m pytest tests/skills/test_hierarchical_development_skill.py -q`

Expected: missing onboarding/responsibility guidance.

- [ ] **Step 3: Write concise operational guidance**

Keep `description: Use when coordinating delegated or durable Hades OrgRuns.`. Add exact branches: missing routing → `setup`; existing routing → preserve; explicit user change → `configure`; orchestrator → structured contract; normal review → parent; dedicated reviewer → escalation.

- [ ] **Step 4: Run the delegation regression set**

Run: `.venv/bin/python -m pytest tests/skills/test_hierarchical_development_skill.py tests/tools/test_delegation_routing.py tests/tools/test_delegation_contract.py tests/tools/test_delegation_capacity.py tests/tools/test_delegation_evidence.py tests/hermes_cli/test_delegation_onboarding.py tests/hermes_cli/test_hades_delegation_cmd.py tests/tools/test_delegate.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add skills/software-development/hierarchical-development/SKILL.md docs/hades/org-run-operations.md tests/skills/test_hierarchical_development_skill.py
git commit -m "docs(hades): teach hierarchical delegation onboarding"
```
