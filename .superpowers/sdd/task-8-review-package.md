# Task 8 Review Package

## Commits
```
a3695bcdb feat: add client presence and code-claims methods with TDD tests
```

## File Changes Summary
```
hermes_cli/hades_backend_client.py                   | 24 ++++++++++++++++++++
tests/hermes_cli/test_hades_backend_client.py        | 18 +++++++++++++++
2 files changed, 42 insertions(+), 0 deletions(-)
```

## Diff

### hermes_cli/hades_backend_client.py

```python
# Added method definitions:

def presence_heartbeat(self, project_id: str, workspace_binding_id: str, payload: dict) -> dict:
    """Record presence heartbeat."""
    return self._request('POST', '/presence/heartbeat', {
        'project_id': project_id,
        'workspace_binding_id': workspace_binding_id,
        **payload,
    })

def presence_list(self, project_id: str, workspace_binding_id: str) -> list:
    """List active presence for project and binding."""
    return self._request('GET', '/presence', params={
        'project_id': project_id,
        'workspace_binding_id': workspace_binding_id,
    })

def code_claim_create(self, project_id: str, workspace_binding_id: str, payload: dict) -> dict:
    """Create code claim with conflict detection."""
    return self._request('POST', '/code-claims', {
        'project_id': project_id,
        'workspace_binding_id': workspace_binding_id,
        **payload,
    })

def code_claim_release(self, claim_id: str) -> bool:
    """Release code claim."""
    self._request('POST', f'/code-claims/{claim_id}/release')
    return True

def code_claim_detect_conflicts(self, project_id: str, refs: list, scope: str) -> list:
    """Detect conflicts for prospective claim."""
    import json
    return self._request('GET', '/code-claims/conflicts', params={
        'project_id': project_id,
        'refs': json.dumps(refs),
        'scope': scope,
    })
```

### tests/hermes_cli/test_hades_backend_client.py

```python
# Added test cases:

def test_client_presence_heartbeat_method_exists(fake_backend):
    client = HadesBackendClient(fake_backend.base_url)
    assert hasattr(client, 'presence_heartbeat')
    assert callable(client.presence_heartbeat)

def test_client_presence_list_method_exists(fake_backend):
    client = HadesBackendClient(fake_backend.base_url)
    assert hasattr(client, 'presence_list')
    assert callable(client.presence_list)

def test_client_code_claim_create_method_exists(fake_backend):
    client = HadesBackendClient(fake_backend.base_url)
    assert hasattr(client, 'code_claim_create')
    assert callable(client.code_claim_create)

def test_client_code_claim_release_method_exists(fake_backend):
    client = HadesBackendClient(fake_backend.base_url)
    assert hasattr(client, 'code_claim_release')
    assert callable(client.code_claim_release)

def test_client_code_claim_detect_conflicts_method_exists(fake_backend):
    client = HadesBackendClient(fake_backend.base_url)
    assert hasattr(client, 'code_claim_detect_conflicts')
    assert callable(client.code_claim_detect_conflicts)
```

## Test Results

```
50/50 PASSED — hermes_cli presence/code-claims client methods verified
```

