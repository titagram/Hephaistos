"""Tests for local code claim wrapper with finally semantics in HadesCoordination."""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock, patch

import pytest

from hermes_cli.hades_backend_client import HadesBackendClient, HadesBackendError
from hermes_cli.hades_coordination import HadesCoordination


@pytest.fixture
def mock_backend_client() -> Mock:
    """Create a mock HadesBackendClient."""
    client = Mock(spec=HadesBackendClient)
    return client


@pytest.fixture
def hades_coordination(mock_backend_client: Mock) -> HadesCoordination:
    """Create a HadesCoordination instance with mock backend."""
    return HadesCoordination(
        project_id="proj_123",
        workspace_binding_id="wb_456",
        agent_id="test-agent",
        backend_client=mock_backend_client,
        ttl_seconds=300,
    )


def test_claim_and_run_creates_claim_and_releases_on_success(
    hades_coordination: HadesCoordination,
    mock_backend_client: Mock,
) -> None:
    """Test that claim_and_run creates a claim, runs the runner, and releases the claim."""
    # Setup mock responses
    claim_response = {
        "id": "claim_789",
        "status": "active",
        "conflicts": [],
    }
    release_response = {
        "id": "claim_789",
        "status": "released",
    }
    mock_backend_client.code_claim_create.return_value = claim_response
    mock_backend_client.code_claim_release.return_value = release_response

    # Define a simple runner
    runner_called = False

    def runner() -> str:
        nonlocal runner_called
        runner_called = True
        return "runner result"

    # Call claim_and_run
    refs = [{"type": "path", "value": "app/Foo.php"}]
    result = hades_coordination.claim_and_run(runner, refs, scope="edit")

    # Verify claim was created
    mock_backend_client.code_claim_create.assert_called_once()
    call_kwargs = mock_backend_client.code_claim_create.call_args[1]
    assert call_kwargs["project_id"] == "proj_123"
    assert call_kwargs["workspace_binding_id"] == "wb_456"
    assert call_kwargs["agent_id"] == "test-agent"
    assert call_kwargs["refs"] == refs
    assert call_kwargs["scope"] == "edit"

    # Verify runner was executed
    assert runner_called

    # Verify claim was released
    mock_backend_client.code_claim_release.assert_called_once()
    release_call_kwargs = mock_backend_client.code_claim_release.call_args[0]
    assert release_call_kwargs[0] == "claim_789"

    # Verify result includes claim and runner result
    assert result["success"] is True
    assert result["runner_result"] == "runner result"
    assert result["claim"] == claim_response
    assert result["conflicts"] == []


def test_claim_and_run_releases_even_when_runner_raises_exception(
    hades_coordination: HadesCoordination,
    mock_backend_client: Mock,
) -> None:
    """CRITICAL TEST: claim_and_run releases the claim in finally, even if runner raises."""
    # Setup mock responses
    claim_response = {
        "id": "claim_xyz",
        "status": "active",
        "conflicts": [],
    }
    release_response = {
        "id": "claim_xyz",
        "status": "released",
    }
    mock_backend_client.code_claim_create.return_value = claim_response
    mock_backend_client.code_claim_release.return_value = release_response

    # Define a runner that raises
    test_exception = ValueError("runner failed")

    def failing_runner() -> None:
        raise test_exception

    # Call claim_and_run and expect the exception to propagate
    refs = [{"type": "path", "value": "app/Bar.php"}]
    with pytest.raises(ValueError, match="runner failed"):
        hades_coordination.claim_and_run(failing_runner, refs, scope="refactor")

    # Verify claim was created
    mock_backend_client.code_claim_create.assert_called_once()

    # CRITICAL: Verify release was called despite the exception
    mock_backend_client.code_claim_release.assert_called_once_with("claim_xyz")


def test_claim_and_run_includes_conflicts_in_result(
    hades_coordination: HadesCoordination,
    mock_backend_client: Mock,
) -> None:
    """Test that conflicts from claim response are included in the result."""
    # Setup mock response with conflicts
    claim_response = {
        "id": "claim_with_conflicts",
        "status": "active",
        "conflicts": [
            {
                "claim_id": "other_claim",
                "agent_id": "other-agent",
                "ref": "app/Foo.php",
                "reason": "Edit conflict",
            }
        ],
    }
    release_response = {
        "id": "claim_with_conflicts",
        "status": "released",
    }
    mock_backend_client.code_claim_create.return_value = claim_response
    mock_backend_client.code_claim_release.return_value = release_response

    def runner() -> str:
        return "result"

    refs = [{"type": "path", "value": "app/Foo.php"}]
    result = hades_coordination.claim_and_run(runner, refs, scope="edit")

    # Verify conflicts are in result
    assert result["conflicts"] == claim_response["conflicts"]
    assert len(result["conflicts"]) == 1
    assert result["conflicts"][0]["agent_id"] == "other-agent"


def test_claim_and_run_with_default_scope_is_edit(
    hades_coordination: HadesCoordination,
    mock_backend_client: Mock,
) -> None:
    """Test that default scope is 'edit' if not specified."""
    claim_response = {"id": "claim_default", "status": "active", "conflicts": []}
    release_response = {"id": "claim_default", "status": "released"}
    mock_backend_client.code_claim_create.return_value = claim_response
    mock_backend_client.code_claim_release.return_value = release_response

    def runner() -> str:
        return "result"

    refs = [{"type": "path", "value": "app/Test.php"}]
    # Don't specify scope
    result = hades_coordination.claim_and_run(runner, refs)

    # Verify default scope was used
    call_kwargs = mock_backend_client.code_claim_create.call_args[1]
    assert call_kwargs["scope"] == "edit"

    # Verify release was called
    mock_backend_client.code_claim_release.assert_called_once()


def test_claim_and_run_propagates_backend_error_on_create(
    hades_coordination: HadesCoordination,
    mock_backend_client: Mock,
) -> None:
    """Test that backend errors during claim creation are propagated."""
    backend_error = HadesBackendError("Backend unavailable", status_code=503)
    mock_backend_client.code_claim_create.side_effect = backend_error

    def runner() -> str:
        return "result"

    refs = [{"type": "path", "value": "app/Test.php"}]

    # Expect the backend error to be raised
    with pytest.raises(HadesBackendError, match="Backend unavailable"):
        hades_coordination.claim_and_run(runner, refs)

    # Verify release was NOT called (claim was never created)
    mock_backend_client.code_claim_release.assert_not_called()


def test_claim_and_run_release_failure_does_not_suppress_runner_exception(
    hades_coordination: HadesCoordination,
    mock_backend_client: Mock,
) -> None:
    """Test that if release fails, the original runner exception is preserved."""
    claim_response = {"id": "claim_fail", "status": "active", "conflicts": []}
    release_error = HadesBackendError("Release failed", status_code=500)

    mock_backend_client.code_claim_create.return_value = claim_response
    mock_backend_client.code_claim_release.side_effect = release_error

    runner_exception = RuntimeError("runner error")

    def failing_runner() -> None:
        raise runner_exception

    refs = [{"type": "path", "value": "app/Test.php"}]

    # The original runner exception should be raised, not the release error
    with pytest.raises(RuntimeError, match="runner error"):
        hades_coordination.claim_and_run(failing_runner, refs)

    # Verify release was attempted
    mock_backend_client.code_claim_release.assert_called_once()
