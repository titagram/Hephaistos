"""Test non-blocking heartbeat loop for Hades presence coordination (Python worker)."""

from __future__ import annotations

import logging
import threading
import time
from unittest.mock import MagicMock, Mock, patch

import pytest

from hermes_cli.hades_coordination import HadesCoordination


class TestHadesCoordinationHeartbeat:
    """Test suite for HadesCoordination heartbeat non-blocking loop."""

    @pytest.fixture
    def mock_backend_client(self):
        """Mock HadesBackendClient for testing."""
        client = MagicMock()
        return client

    @pytest.fixture
    def coordination(self, mock_backend_client):
        """Create a HadesCoordination instance with mocked backend."""
        return HadesCoordination(
            project_id="test-project",
            workspace_binding_id="test-workspace",
            agent_id="test-agent",
            backend_client=mock_backend_client,
            heartbeat_interval=0.1,  # 100ms for faster tests
        )

    def test_initialization(self, coordination, mock_backend_client):
        """Test HadesCoordination initializes with correct state."""
        assert coordination.project_id == "test-project"
        assert coordination.workspace_binding_id == "test-workspace"
        assert coordination.agent_id == "test-agent"
        assert coordination.backend_client == mock_backend_client
        assert coordination._heartbeat_thread is None
        assert coordination._stop_event.is_set() is False

    def test_set_git_state_stores_state(self, coordination):
        """Test set_git_state stores git state."""
        git_state = {
            "current_branch": "main",
            "last_head_sha": "abc123",
            "dirty_status": False,
        }
        coordination.set_git_state(**git_state)

        # Retrieve state via internal method
        assert coordination._git_state["current_branch"] == "main"
        assert coordination._git_state["last_head_sha"] == "abc123"
        assert coordination._git_state["dirty_status"] is False

    def test_set_git_state_thread_safe(self, coordination):
        """Test set_git_state is thread-safe with lock."""
        results = []

        def set_state_concurrent(idx):
            coordination.set_git_state(
                current_branch=f"branch-{idx}",
                last_head_sha=f"sha-{idx}",
                dirty_status=idx % 2 == 0,
            )
            results.append(idx)

        threads = [
            threading.Thread(target=set_state_concurrent, args=(i,))
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All threads completed without error
        assert len(results) == 5

    def test_start_heartbeat_loop_creates_thread(self, coordination):
        """Test start_heartbeat_loop creates and starts a background thread."""
        coordination.set_git_state(
            current_branch="main",
            last_head_sha="abc123",
            dirty_status=False,
        )

        coordination.start_heartbeat_loop()

        # Thread should be created and running
        assert coordination._heartbeat_thread is not None
        assert isinstance(coordination._heartbeat_thread, threading.Thread)
        assert coordination._heartbeat_thread.is_alive()

        # Cleanup
        coordination.stop_heartbeat_loop(timeout=1.0)

    def test_stop_heartbeat_loop_graceful_shutdown(self, coordination, mock_backend_client):
        """Test stop_heartbeat_loop gracefully shuts down the thread."""
        coordination.set_git_state(
            current_branch="main",
            last_head_sha="abc123",
            dirty_status=False,
        )
        mock_backend_client.presence_heartbeat.return_value = {"id": "beat-1"}

        coordination.start_heartbeat_loop()
        assert coordination._heartbeat_thread.is_alive()

        # Stop should wait for thread termination
        coordination.stop_heartbeat_loop(timeout=2.0)

        # Wait a bit to ensure thread exits
        time.sleep(0.2)

        # Thread should be terminated
        assert coordination._heartbeat_thread is None or not coordination._heartbeat_thread.is_alive()

    def test_stop_heartbeat_loop_timeout(self, coordination):
        """Test stop_heartbeat_loop respects timeout."""
        # Create a mock thread that never exits
        mock_thread = Mock()
        mock_thread.is_alive.return_value = True
        coordination._heartbeat_thread = mock_thread
        coordination._stop_event.clear()

        start = time.time()
        coordination.stop_heartbeat_loop(timeout=0.1)
        elapsed = time.time() - start

        # Should respect timeout (roughly, allowing for test timing variations)
        assert elapsed < 1.0

    def test_heartbeat_loop_sends_presence(self, coordination, mock_backend_client):
        """Test heartbeat loop sends presence to backend."""
        coordination.set_git_state(
            current_branch="main",
            last_head_sha="abc123",
            dirty_status=False,
        )
        mock_backend_client.presence_heartbeat.return_value = {"id": "beat-1"}

        coordination.start_heartbeat_loop()

        # Let it run for a short time
        time.sleep(0.3)

        # Should have called presence_heartbeat at least once
        assert mock_backend_client.presence_heartbeat.call_count >= 1

        # Verify call parameters
        call_args = mock_backend_client.presence_heartbeat.call_args
        assert call_args is not None
        payload = call_args[1]  # kwargs
        assert payload["project_id"] == "test-project"
        assert payload["workspace_binding_id"] == "test-workspace"
        assert payload["agent_id"] == "test-agent"
        assert payload["current_branch"] == "main"
        assert payload["last_head_sha"] == "abc123"
        assert payload["dirty_status"] is False

        coordination.stop_heartbeat_loop(timeout=1.0)

    def test_heartbeat_loop_non_blocking_on_error(self, coordination, mock_backend_client, caplog):
        """Test heartbeat loop doesn't block runner on backend error."""
        coordination.set_git_state(
            current_branch="main",
            last_head_sha="abc123",
            dirty_status=False,
        )

        # First call fails, subsequent calls succeed
        from hermes_cli.hades_backend_client import HadesBackendError
        mock_backend_client.presence_heartbeat.side_effect = [
            HadesBackendError("Backend offline"),
            {"id": "beat-2"},
            {"id": "beat-3"},
        ]

        with caplog.at_level(logging.ERROR):
            coordination.start_heartbeat_loop()

            # Let it run for a bit to execute multiple heartbeats
            time.sleep(0.35)

            coordination.stop_heartbeat_loop(timeout=1.0)

        # Error should be logged but not re-raised
        assert "heartbeat" in caplog.text.lower() or "error" in caplog.text.lower()

        # Loop should have retried despite error
        assert mock_backend_client.presence_heartbeat.call_count >= 2

    def test_heartbeat_loop_handles_network_exception(self, coordination, mock_backend_client, caplog):
        """Test heartbeat loop handles network exceptions gracefully."""
        coordination.set_git_state(
            current_branch="main",
            last_head_sha="abc123",
            dirty_status=False,
        )

        # Simulate network error then recovery
        import httpx
        mock_backend_client.presence_heartbeat.side_effect = [
            httpx.ConnectError("Network unreachable"),
            {"id": "beat-1"},
        ]

        with caplog.at_level(logging.ERROR):
            coordination.start_heartbeat_loop()

            # Let it run for multiple iterations
            time.sleep(0.35)

            coordination.stop_heartbeat_loop(timeout=1.0)

        # Should have retried
        assert mock_backend_client.presence_heartbeat.call_count >= 2

    def test_git_state_read_thread_safe(self, coordination):
        """Test git_state is protected by lock during concurrent reads."""
        coordination.set_git_state(
            current_branch="main",
            last_head_sha="abc123",
            dirty_status=False,
        )

        read_results = []

        def read_git_state():
            state = coordination._git_state.copy()
            read_results.append(state)

        threads = [
            threading.Thread(target=read_git_state)
            for _ in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All reads should have succeeded
        assert len(read_results) == 5

    def test_heartbeat_respects_ttl_seconds(self, coordination, mock_backend_client):
        """Test heartbeat sends configurable TTL."""
        coordination.set_git_state(
            current_branch="main",
            last_head_sha="abc123",
            dirty_status=False,
        )
        mock_backend_client.presence_heartbeat.return_value = {"id": "beat-1"}

        # Create new instance with custom TTL
        coord = HadesCoordination(
            project_id="test-project",
            workspace_binding_id="test-workspace",
            agent_id="test-agent",
            backend_client=mock_backend_client,
            heartbeat_interval=0.1,
            ttl_seconds=600,
        )
        coord.set_git_state(
            current_branch="main",
            last_head_sha="abc123",
            dirty_status=False,
        )

        coord.start_heartbeat_loop()
        time.sleep(0.2)
        coord.stop_heartbeat_loop(timeout=1.0)

        # Verify TTL in call
        call_args = mock_backend_client.presence_heartbeat.call_args
        assert call_args[1]["ttl_seconds"] == 600

    def test_multiple_start_calls_dont_create_multiple_threads(self, coordination, mock_backend_client):
        """Test calling start_heartbeat_loop multiple times doesn't create duplicate threads."""
        coordination.set_git_state(
            current_branch="main",
            last_head_sha="abc123",
            dirty_status=False,
        )
        mock_backend_client.presence_heartbeat.return_value = {"id": "beat-1"}

        first_thread = None
        coordination.start_heartbeat_loop()
        first_thread = coordination._heartbeat_thread

        # Try starting again
        coordination.start_heartbeat_loop()

        # Should be the same thread (idempotent)
        assert coordination._heartbeat_thread == first_thread

        coordination.stop_heartbeat_loop(timeout=1.0)

    def test_heartbeat_loop_continues_on_exception(self, coordination, mock_backend_client, caplog):
        """Test heartbeat loop continues despite arbitrary exceptions."""
        coordination.set_git_state(
            current_branch="main",
            last_head_sha="abc123",
            dirty_status=False,
        )

        # Raise exception then succeed
        mock_backend_client.presence_heartbeat.side_effect = [
            RuntimeError("Unexpected error"),
            {"id": "beat-1"},
            {"id": "beat-2"},
        ]

        with caplog.at_level(logging.ERROR):
            coordination.start_heartbeat_loop()
            time.sleep(0.35)
            coordination.stop_heartbeat_loop(timeout=1.0)

        # Should have made multiple calls despite error
        assert mock_backend_client.presence_heartbeat.call_count >= 2
