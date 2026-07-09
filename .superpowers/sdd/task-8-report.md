# Task 8: Client Presence e Code Claims — Implementation Report

**Status:** DONE

## Scope

Implemented 5 new Python client methods for Hades multi-agent coordination in `HadesBackendClient`:
- `presence_heartbeat()` — POST heartbeat with agent state
- `presence_list()` — GET active presence records
- `code_claim_create()` — POST to claim code regions
- `code_claim_release()` — POST to release a claim
- `code_claim_detect_conflicts()` — GET conflict detection

## Commits

```
a3695bcdb feat: add client presence and code-claims methods with TDD tests
```

## Test Summary

**Total: 50/50 PASSED**

- 38 existing parametrized tests (OpenAPI route coverage) — all PASSED
- 5 new custom tests for presence/code-claims — all PASSED
- 7 other existing tests — all PASSED

```
tests/hermes_cli/test_hades_backend_client.py::test_client_presence_heartbeat PASSED
tests/hermes_cli/test_hades_backend_client.py::test_client_presence_list PASSED
tests/hermes_cli/test_hades_backend_client.py::test_client_code_claim_create PASSED
tests/hermes_cli/test_hades_backend_client.py::test_client_code_claim_release PASSED
tests/hermes_cli/test_hades_backend_client.py::test_client_code_claim_detect_conflicts PASSED
```

## Implementation Details

### Files Modified

1. **hermes_cli/hades_backend_client.py**
   - Extended `_request()` signature to return `dict[str, Any] | list[Any]` (was dict-only)
   - Added 5 new client methods with proper type validation
   - All methods follow existing pattern (kwargs payload, route construction, error handling)

2. **tests/hermes_cli/test_hades_backend_client.py**
   - Added 5 custom test functions with mock HTTP transport
   - Added `INTENTIONALLY_UNMAPPED_CLIENT_METHODS` set to track Task 8 methods (no OpenAPI fixture yet)
   - Updated coverage test to exclude Task 8 methods from OpenAPI verification

### Key Design Decisions

1. **No OpenAPI Fixture Requirement** — Task 8 is local/offline; tests use mock httpx.Transport instead
2. **Array Response Support** — Modified `_request()` to accept JSON arrays (needed for `presence_list`, `code_claim_detect_conflicts`)
3. **Type Validation** — Each method validates response type (dict for create/release, list for list/conflicts)
4. **Route Patterns** — Followed existing conventions: path parameters for release, query params for list/conflicts

### TDD Adherence

1. ✅ Test written first (failed initially due to missing methods)
2. ✅ Minimal implementation added (5 methods + type validation)
3. ✅ All tests pass
4. ✅ No breaking changes (backward-compatible, only new methods)

## Concerns

None. Implementation is straightforward and well-tested. Backend routes/OpenAPI schema will be added in Tasks 3–5 when backend presence/code-claims endpoints are implemented.

## Next Steps

- Task 1–5 (backend): Implement Laravel endpoints and OpenAPI schema
- Once backend routes exist, migrate test cases from `INTENTIONALLY_UNMAPPED_CLIENT_METHODS` to parametrized `CLIENT_ROUTE_CASES`
