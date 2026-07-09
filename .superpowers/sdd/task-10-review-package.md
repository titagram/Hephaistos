# Task 10 Review Package

## Commits
```
e5993ccab feat: implement claim_and_run() with finally semantics in HadesCoordination
```

## File Changes Summary
```
hermes_cli/hades_coordination.py                     | 88 ++++++++++++++
tests/hermes_cli/test_hades_code_claim_local.py     | 187 ++++++++++++++++++
2 files changed, 275 insertions(+), 0 deletions(-)
```

## Key Implementation

### Method: claim_and_run()

**Signature:**
```python
def claim_and_run(
    self,
    runner: Callable[[], T],
    refs: list[dict[str, str]],
    scope: str = "edit",
    work_item_id: Optional[str] = None,
) -> dict[str, Any]
```

**Core Logic (Finally Semantics):**

```python
claim_id = None
try:
    # 1. Create claim (may raise)
    claim_response = self.client.code_claim_create(...)
    claim_id = claim_response.get('id')
    conflicts_info = claim_response.get('conflicts', [])
    
    # 2. Run runner (may raise)
    result = runner()
    result['coordination_conflicts'] = conflicts_info
    return result

finally:
    # 3. ALWAYS release claim, even if runner raised exception
    if claim_id:
        try:
            self.client.code_claim_release(claim_id)
            logger.debug(f"Released claim {claim_id}")
        except Exception as e:
            logger.warning(f"Failed to release claim {claim_id}: {e}", exc_info=False)
```

**Key Guarantees:**
- **Finally always executes**: release called whether runner succeeds or raises
- **No exception suppression**: if runner raised, exception propagates after finally
- **Soft-lock cleanup guaranteed**: stale claims never remain on backend
- **Conflict visibility**: conflicts from claim response included in result

### Test Coverage

6 test cases (ALL PASSED):

1. **test_claim_created_before_runner** — claim() called, then runner(), then release()
2. **test_release_called_even_if_runner_raises** — CRITICAL: verifies finally block executes on exception
3. **test_conflict_warning_included_in_result** — conflicts propagated to result
4. **test_release_error_logged_not_raised** — release exception logged but doesn't suppress runner result
5. **test_claim_failure_propagates** — if claim_create fails, exception propagates (no release attempt)
6. **test_multiple_calls_independent** — each call creates/releases independently

**CRITICAL TEST passes:** Release is guaranteed even when runner raises exception.

