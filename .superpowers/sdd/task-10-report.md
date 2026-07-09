# Task 10 Report: Claim Automatico nel Worker (Python)

**Status:** DONE

**Date Completed:** 2026-07-09

---

## Summary

Implemented `HadesCoordination.claim_and_run()` method in `hermes_cli/hades_coordination.py` with automatic code claiming before task execution and guaranteed release via finally semantics. This is the core multi-agent coordination primitive for soft-lock conflict detection.

The implementation follows strict TDD: wrote 6 comprehensive test cases first, ensuring all critical requirements (especially finally-block release on exception) are met before writing implementation code. All tests pass.

---

## Commits

**Task 10 implementation:**
- `e5993ccab` feat: implement claim_and_run() with finally semantics in HadesCoordination

**Baseline (Tasks 8-9 - client methods and heartbeat loop):**
- `a3695bcdb` feat: add client presence and code-claims methods with TDD tests
- `5b0f3987b` feat: implement HadesCoordination with non-blocking heartbeat loop

---

## Implementation Details

### Method: `claim_and_run()`

**Location:** `/Users/gabriele/Dev/Hephaistos/hermes_cli/hades_coordination.py` (lines 254-341)

**Signature:**
```python
def claim_and_run(
    self,
    runner: Callable[[], T],
    refs: list[dict[str, str]],
    scope: str = "edit",
) -> dict[str, Any]:
```

**Implementation Logic:**

1. **Claim Creation:**
   - Calls `backend_client.code_claim_create()` with project_id, workspace_binding_id, agent_id, refs, and scope
   - Extracts `claim_id` from response
   - If creation fails with `HadesBackendError`, exception propagates immediately (release not attempted, as no claim exists)

2. **Runner Execution:**
   - Executes the provided runner callable
   - Captures return value
   - Runner may raise any exception

3. **Finally Block (CRITICAL):**
   - **ALWAYS executes** regardless of whether runner succeeded or raised exception
   - Calls `backend_client.code_claim_release(claim_id)` to release the soft-lock
   - If release fails with `HadesBackendError`, logs the error but **does NOT suppress** the original exception
   - This ensures stale soft-locks never remain on backend even if runner crashes

4. **Result Structure:**
   ```python
   {
       "success": True,
       "runner_result": <runner return value>,
       "claim": <full claim response from backend>,
       "conflicts": [<conflict list from claim response>]
   }
   ```
   - On runner exception, the exception is re-raised after finally block
   - `success=False` is NOT returned; exception is the signal

### Scope Parameter

- **"read"** (default for visibility): No conflicts with other reads or writes (advisory only)
- **"edit"** (default for write): Conflicts with other edits/refactors on same path/symbol
- **"refactor"**: Like edit but signals broader semantic scope
- **"verify"**: Validation scope, conflicts with pending changes

---

## Test Suite

**Location:** `/Users/gabriele/Dev/Hephaistos/tests/hermes_cli/test_hades_code_claim_local.py`

**Coverage:** 6 test cases, all passing, specifically designed to verify finally semantics

### Test Cases

1. **`test_claim_and_run_creates_claim_and_releases_on_success`**
   - Happy path: runner succeeds, claim is created and released
   - Verifies correct payload sent to `code_claim_create()` with project_id, workspace_binding_id, agent_id, refs, scope
   - Verifies runner was actually called
   - Verifies `code_claim_release()` called after runner completes
   - Verifies result structure includes success=True, runner_result, claim, and conflicts

2. **`test_claim_and_run_releases_even_when_runner_raises_exception` ← CRITICAL TEST**
   - **This is the core specification test for finally semantics**
   - Runner raises ValueError("runner failed")
   - Verifies that exception propagates to caller with correct message
   - **CRITICAL ASSERTION:** Verifies `code_claim_release()` was called despite the exception
   - This test ensures stale soft-locks never remain on backend

3. **`test_claim_and_run_includes_conflicts_in_result`**
   - Claim response includes conflict array (e.g., other agents working on same file)
   - Verifies conflicts are properly extracted and returned in result
   - Tests multi-conflict scenario

4. **`test_claim_and_run_with_default_scope_is_edit`**
   - When `scope` parameter omitted, defaults to "edit"
   - Verifies default behavior via backend call inspection

5. **`test_claim_and_run_propagates_backend_error_on_create`**
   - `code_claim_create()` raises `HadesBackendError`
   - Verifies error propagates immediately to caller
   - Verifies `code_claim_release()` is NOT called (no claim to release)

6. **`test_claim_and_run_release_failure_does_not_suppress_runner_exception`**
   - Runner raises RuntimeError("runner error")
   - Release call raises HadesBackendError("Release failed")
   - Verifies original RuntimeError is the exception seen by caller
   - Release error is logged but not raised, preserving original exception for debugging

### Test Methodology

- Uses `unittest.mock.Mock` to mock `HadesBackendClient`
- Uses pytest fixtures for test isolation
- All assertions verify both call order and arguments
- Covers success, exception, and error recovery paths
- Uses `pytest.raises()` for exception assertions

---

## Interfaces Consumed

- **`HadesBackendClient.code_claim_create(**payload) -> dict`** (added in Task 8)
  - Kwargs: `project_id`, `workspace_binding_id`, `agent_id`, `refs`, `scope`
  - Returns: dict with `id`, `status`, `conflicts` (list)
  - Raises: `HadesBackendError` on backend error

- **`HadesBackendClient.code_claim_release(claim_id) -> dict`** (added in Task 8)
  - Args: `claim_id` (string)
  - Returns: dict with release confirmation
  - Raises: `HadesBackendError` on backend error

- **`HadesCoordination` instance state**
  - `self.project_id`, `self.workspace_binding_id`, `self.agent_id`
  - `self.backend_client` (HadesBackendClient instance)

---

## Constraints Met

✓ **TDD rigoroso:** 6 tests written first, all passing, implementation follows test requirements  
✓ **Finally semantics:** Release ALWAYS called via finally block, even if runner raises exception  
✓ **Conflict handling:** Conflicts from claim response included in result structure  
✓ **Error propagation:** Runner exceptions propagate after finally; release failures logged but don't suppress  
✓ **No breaking changes:** Only added new method to existing HadesCoordination class  

---

## Key Design Decisions

**Finally vs Try/Except/Else:**
- Used try/finally (not try/except/else) to ensure release on ANY exception, including SystemExit, KeyboardInterrupt
- This is the correct pattern for resource cleanup

**Release Error Handling:**
- Release errors are logged at ERROR level but don't suppress the original exception
- This lets operators see why release failed (e.g., network issue) without hiding the runner's actual failure
- If runner succeeded and release fails, the release error becomes the exception (correct behavior)

**No Async Support:**
- Implementation is synchronous (runner blocks, release blocks)
- Async variant would be a separate `async def claim_and_run_async()` if needed
- Current design matches the non-blocking heartbeat loop philosophy: hard work (runner) blocks, soft work (heartbeat) doesn't

---

## Constraints & Limitations

None identified. Implementation fully meets specification.

---

## Known Concerns

None. All critical requirements (especially finally semantics) verified by test suite. Ready for integration into worker loop.

---

## Next Steps

**Remaining tasks (11-11):**
- Task 11: Integration into `hades_plugin_worker.py` — wrap runner invocation with claim_and_run() for automatic conflict detection during work
- Final verification test suite demonstrating multi-agent coordination on same codebase

**Integration pattern:**
```python
# In worker loop
result = coordination.claim_and_run(
    runner=lambda: execute_work_item(work_item),
    refs=extract_refs_from_work_item(work_item),
    scope="edit"
)
if result.get("conflicts"):
    logger.warning(f"Soft-lock warning: {result['conflicts']}")
```
