"""Tests for web backend client configuration and singleton behavior.

Coverage:
  _get_firecrawl_client() — configuration matrix, singleton caching,
  constructor failure recovery, return value verification, edge cases.
  _get_backend() — backend selection logic with env var combinations.
  _get_parallel_client() — Parallel client configuration, singleton caching.
  check_web_api_key() — unified availability check across all web backends.
"""

import importlib
import json
import os
import sys
import types
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


class TestFirecrawlClientConfig:
    """Test suite for Firecrawl client initialization."""

    def setup_method(self):
        """Reset client and env vars before each test."""
        import tools.web_tools
        tools.web_tools._firecrawl_client = None
        tools.web_tools._firecrawl_client_config = None
        for key in (
            "FIRECRAWL_API_KEY",
            "FIRECRAWL_API_URL",
            "FIRECRAWL_GATEWAY_URL",
            "TOOL_GATEWAY_DOMAIN",
            "TOOL_GATEWAY_SCHEME",
            "TOOL_GATEWAY_USER_TOKEN",
        ):
            os.environ.pop(key, None)
        # Enable managed tools by default for these tests — patch both the
        # local web_tools import and the managed_tool_gateway import so the
        # full firecrawl client init path sees True.
        self._managed_patchers = [
            patch("tools.web_tools.managed_nous_tools_enabled", return_value=True),
            patch("tools.managed_tool_gateway.managed_nous_tools_enabled", return_value=True),
        ]
        for p in self._managed_patchers:
            p.start()

    def teardown_method(self):
        """Reset client after each test."""
        import tools.web_tools
        tools.web_tools._firecrawl_client = None
        tools.web_tools._firecrawl_client_config = None
        for key in (
            "FIRECRAWL_API_KEY",
            "FIRECRAWL_API_URL",
            "FIRECRAWL_GATEWAY_URL",
            "TOOL_GATEWAY_DOMAIN",
            "TOOL_GATEWAY_SCHEME",
            "TOOL_GATEWAY_USER_TOKEN",
        ):
            os.environ.pop(key, None)
        for p in self._managed_patchers:
            p.stop()

    # ── Configuration matrix ─────────────────────────────────────────

    def test_no_config_raises_with_helpful_message(self):
        """Neither key nor URL → ValueError with guidance."""
        with patch("tools.web_tools.Firecrawl"):
            with patch("tools.web_tools._read_nous_access_token", return_value=None):
                from tools.web_tools import _get_firecrawl_client
                with pytest.raises(ValueError, match="FIRECRAWL_API_KEY"):
                    _get_firecrawl_client()

    def test_tool_gateway_domain_builds_firecrawl_gateway_origin(self):
        """Shared gateway domain should derive the Firecrawl vendor hostname."""
        with patch.dict(os.environ, {"TOOL_GATEWAY_DOMAIN": "nousresearch.com"}):
            with patch("tools.web_tools._read_nous_access_token", return_value="nous-token"):
                with patch("tools.web_tools.Firecrawl") as mock_fc:
                    from tools.web_tools import _get_firecrawl_client
                    result = _get_firecrawl_client()
                    mock_fc.assert_called_once_with(
                        api_key="nous-token",
                        api_url="https://firecrawl-gateway.nousresearch.com",
                    )
                    assert result is mock_fc.return_value

    def test_tool_gateway_scheme_can_switch_derived_gateway_origin_to_http(self):
        """Shared gateway scheme should allow local plain-http vendor hosts."""
        with patch.dict(os.environ, {
            "TOOL_GATEWAY_DOMAIN": "nousresearch.com",
            "TOOL_GATEWAY_SCHEME": "http",
        }):
            with patch("tools.web_tools._read_nous_access_token", return_value="nous-token"):
                with patch("tools.web_tools.Firecrawl") as mock_fc:
                    from tools.web_tools import _get_firecrawl_client
                    result = _get_firecrawl_client()
                    mock_fc.assert_called_once_with(
                        api_key="nous-token",
                        api_url="http://firecrawl-gateway.nousresearch.com",
                    )
                    assert result is mock_fc.return_value

    def test_invalid_tool_gateway_scheme_raises(self):
        """Unexpected shared gateway schemes should fail fast."""
        with patch.dict(os.environ, {
            "TOOL_GATEWAY_DOMAIN": "nousresearch.com",
            "TOOL_GATEWAY_SCHEME": "ftp",
        }):
            with patch("tools.web_tools._read_nous_access_token", return_value="nous-token"):
                from tools.web_tools import _get_firecrawl_client
                with pytest.raises(ValueError, match="TOOL_GATEWAY_SCHEME"):
                    _get_firecrawl_client()

    def test_explicit_firecrawl_gateway_url_takes_precedence(self):
        """An explicit Firecrawl gateway origin should override the shared domain."""
        with patch.dict(os.environ, {
            "FIRECRAWL_GATEWAY_URL": "https://firecrawl-gateway.localhost:3009/",
            "TOOL_GATEWAY_DOMAIN": "nousresearch.com",
        }):
            with patch("tools.web_tools._read_nous_access_token", return_value="nous-token"):
                with patch("tools.web_tools.Firecrawl") as mock_fc:
                    from tools.web_tools import _get_firecrawl_client
                    _get_firecrawl_client()
                    mock_fc.assert_called_once_with(
                        api_key="nous-token",
                        api_url="https://firecrawl-gateway.localhost:3009",
                    )

    def test_default_gateway_domain_targets_nous_production_origin(self):
        """Default gateway origin should point at the Firecrawl vendor hostname."""
        with patch("tools.web_tools._read_nous_access_token", return_value="nous-token"):
            with patch("tools.web_tools.Firecrawl") as mock_fc:
                from tools.web_tools import _get_firecrawl_client
                _get_firecrawl_client()
                mock_fc.assert_called_once_with(
                    api_key="nous-token",
                    api_url="https://firecrawl-gateway.nousresearch.com",
                )

    def test_nous_auth_token_respects_hermes_home_override(self, tmp_path):
        """Auth lookup should read from HERMES_HOME/auth.json, not ~/.hermes/auth.json."""
        real_home = tmp_path / "real-home"
        (real_home / ".hermes").mkdir(parents=True)

        hermes_home = tmp_path / "hermes-home"
        hermes_home.mkdir()
        (hermes_home / "auth.json").write_text(json.dumps({
            "providers": {
                "nous": {
                    "access_token": "nous-token",
                }
            }
        }))

        with patch.dict(os.environ, {
            "HOME": str(real_home),
            "HERMES_HOME": str(hermes_home),
        }, clear=False):
            import tools.web_tools
            importlib.reload(tools.web_tools)
            assert tools.web_tools._read_nous_access_token() == "nous-token"

    def test_check_auxiliary_model_re_resolves_backend_each_call(self):
        """Availability checks should not be pinned to module import state."""
        import tools.web_tools

        # Simulate the pre-fix import-time cache slot for regression coverage.
        tools.web_tools.__dict__["_aux_async_client"] = None

        with patch(
            "tools.web_tools.get_async_text_auxiliary_client",
            side_effect=[(None, None), (MagicMock(base_url="https://api.openrouter.ai/v1"), "test-model")],
        ):
            assert tools.web_tools.check_auxiliary_model() is False
            assert tools.web_tools.check_auxiliary_model() is True

    @pytest.mark.asyncio
    async def test_summarizer_re_resolves_backend_after_initial_unavailable_state(self):
        """Summarization should pick up a backend that becomes available later in-process."""
        import tools.web_tools

        tools.web_tools.__dict__["_aux_async_client"] = None

        response = MagicMock()
        response.choices = [MagicMock(message=MagicMock(content="summary text"))]

        with patch(
            "tools.web_tools._resolve_web_extract_auxiliary",
            side_effect=[(None, None, {}), (MagicMock(base_url="https://api.openrouter.ai/v1"), "test-model", {})],
        ), patch(
            "tools.web_tools.async_call_llm",
            new=AsyncMock(return_value=response),
        ) as mock_async_call:
            assert tools.web_tools.check_auxiliary_model() is False
            result = await tools.web_tools._call_summarizer_llm(
                "Some content worth summarizing",
                "Source: https://example.com\n\n",
                None,
            )

        assert result == "summary text"
        mock_async_call.assert_awaited_once()

    # ── Singleton caching ────────────────────────────────────────────

    def test_singleton_returns_same_instance(self):
        """Second call returns cached client without re-constructing."""
        with patch.dict(os.environ, {"FIRECRAWL_API_KEY": "fc-test"}):
            with patch("tools.web_tools.Firecrawl") as mock_fc:
                from tools.web_tools import _get_firecrawl_client
                client1 = _get_firecrawl_client()
                client2 = _get_firecrawl_client()
                assert client1 is client2
                mock_fc.assert_called_once()  # constructed only once

    def test_constructor_failure_allows_retry(self):
        """If Firecrawl() raises, next call should retry (not return None)."""
        import tools.web_tools
        with patch.dict(os.environ, {"FIRECRAWL_API_KEY": "fc-test"}):
            with patch("tools.web_tools.Firecrawl") as mock_fc:
                mock_fc.side_effect = [RuntimeError("init failed"), MagicMock()]
                from tools.web_tools import _get_firecrawl_client

                with pytest.raises(RuntimeError):
                    _get_firecrawl_client()

                # Client stayed None, so retry should work
                assert tools.web_tools._firecrawl_client is None
                result = _get_firecrawl_client()
                assert result is not None

    # ── Edge cases ───────────────────────────────────────────────────

    def test_empty_string_key_no_url_raises(self):
        """FIRECRAWL_API_KEY='' with no URL → should raise."""
        with patch.dict(os.environ, {"FIRECRAWL_API_KEY": ""}):
            with patch("tools.web_tools.Firecrawl"):
                with patch("tools.web_tools._read_nous_access_token", return_value=None):
                    from tools.web_tools import _get_firecrawl_client
                    with pytest.raises(ValueError):
                        _get_firecrawl_client()


class TestBackendSelection:
    """Test suite for _get_backend() backend selection logic.

    The backend is configured via config.yaml (web.backend), set by
    ``hermes tools``.  Falls back to key-based detection for legacy/manual
    setups.
    """

    _ENV_KEYS = (
        "EXA_API_KEY",
        "PARALLEL_API_KEY",
        "FIRECRAWL_API_KEY",
        "FIRECRAWL_API_URL",
        "FIRECRAWL_GATEWAY_URL",
        "TOOL_GATEWAY_DOMAIN",
        "TOOL_GATEWAY_SCHEME",
        "TOOL_GATEWAY_USER_TOKEN",
        "TAVILY_API_KEY",
    )

    def setup_method(self):
        for key in self._ENV_KEYS:
            os.environ.pop(key, None)
        self._managed_patchers = [
            patch("tools.web_tools.managed_nous_tools_enabled", return_value=True),
            patch("tools.managed_tool_gateway.managed_nous_tools_enabled", return_value=True),
        ]
        for p in self._managed_patchers:
            p.start()

    def teardown_method(self):
        for key in self._ENV_KEYS:
            os.environ.pop(key, None)
        for p in self._managed_patchers:
            p.stop()

    # ── Config-based selection (web.backend in config.yaml) ───────────

    def test_config_parallel(self):
        """web.backend=parallel in config → 'parallel' regardless of keys."""
        from tools.web_tools import _get_backend
        with patch("tools.web_tools._load_web_config", return_value={"backend": "parallel"}):
            assert _get_backend() == "parallel"

    @pytest.mark.parametrize("backend", ["exa", "firecrawl", "tavily"])
    def test_excluded_config_backend_falls_through(self, backend):
        """Excluded legacy backends in config do not become active in Hades."""
        from tools.web_tools import _get_backend
        with patch("tools.web_tools._load_web_config", return_value={"backend": backend}), \
             patch.dict(os.environ, {"PARALLEL_API_KEY": "test-key"}):
            assert _get_backend() == "parallel"

    def test_config_case_insensitive(self):
        """web.backend=Parallel (mixed case) → 'parallel'."""
        from tools.web_tools import _get_backend
        with patch("tools.web_tools._load_web_config", return_value={"backend": "Parallel"}):
            assert _get_backend() == "parallel"

    def test_excluded_config_backend_case_insensitive(self):
        """Excluded legacy backend names are ignored case-insensitively."""
        from tools.web_tools import _get_backend
        with patch("tools.web_tools._load_web_config", return_value={"backend": "Tavily"}):
            assert _get_backend() == "ddgs"

    # ── Fallback (no web.backend in config) ───────────────────────────

    def test_fallback_parallel_only_key(self):
        """Only PARALLEL_API_KEY set → 'parallel'."""
        from tools.web_tools import _get_backend
        with patch("tools.web_tools._load_web_config", return_value={}), \
             patch.dict(os.environ, {"PARALLEL_API_KEY": "test-key"}):
            assert _get_backend() == "parallel"

    @pytest.mark.parametrize("env_key", ["EXA_API_KEY", "FIRECRAWL_API_KEY", "TAVILY_API_KEY"])
    def test_excluded_provider_keys_do_not_select_backend(self, env_key):
        """Excluded provider keys no longer make those providers auto-selected."""
        from tools.web_tools import _get_backend
        with patch("tools.web_tools._load_web_config", return_value={}), \
             patch("tools.web_tools._ddgs_package_importable", return_value=False), \
             patch.dict(os.environ, {env_key: "legacy-key"}):
            assert _get_backend() == "ddgs"

    def test_fallback_parallel_beats_firecrawl_direct(self):
        """Parallel + excluded legacy keys → parallel."""
        from tools.web_tools import _get_backend
        with patch("tools.web_tools._load_web_config", return_value={}), \
             patch.dict(os.environ, {"PARALLEL_API_KEY": "test-key", "FIRECRAWL_API_KEY": "fc-test"}):
            assert _get_backend() == "parallel"

    def test_fallback_no_keys_defaults_to_ddgs(self):
        """No retained keys, no config → ddgs."""
        from tools.web_tools import _get_backend
        with patch("tools.web_tools._load_web_config", return_value={}), \
             patch("tools.web_tools._ddgs_package_importable", return_value=False):
            assert _get_backend() == "ddgs"

    def test_invalid_config_falls_through_to_fallback(self):
        """web.backend=invalid → ignored, uses key-based fallback."""
        from tools.web_tools import _get_backend
        with patch("tools.web_tools._load_web_config", return_value={"backend": "nonexistent"}), \
             patch.dict(os.environ, {"PARALLEL_API_KEY": "test-key"}):
            assert _get_backend() == "parallel"

    def test_managed_gateway_does_not_preempt_explicit_parallel(self):
        """Managed gateway probes do not beat a retained explicit provider."""
        from tools.web_tools import _get_backend
        with patch("tools.web_tools._load_web_config", return_value={}), \
             patch("tools.web_tools._is_tool_gateway_ready", return_value=True), \
             patch.dict(os.environ, {"PARALLEL_API_KEY": "par-test"}):
            assert _get_backend() == "parallel"

    def test_managed_gateway_only_falls_through_to_ddgs(self):
        """A managed Firecrawl gateway is not a Hades web backend."""
        from tools.web_tools import _get_backend
        with patch("tools.web_tools._load_web_config", return_value={}), \
             patch("tools.web_tools._is_tool_gateway_ready", return_value=True):
            assert _get_backend() == "ddgs"


class TestParallelClientConfig:
    """Test suite for Parallel client initialization."""

    def setup_method(self):
        import tools.web_tools
        tools.web_tools._parallel_client = None
        os.environ.pop("PARALLEL_API_KEY", None)
        fake_parallel = types.ModuleType("parallel")

        class Parallel:
            def __init__(self, api_key):
                self.api_key = api_key

        class AsyncParallel:
            def __init__(self, api_key):
                self.api_key = api_key

        fake_parallel.Parallel = Parallel
        fake_parallel.AsyncParallel = AsyncParallel
        sys.modules["parallel"] = fake_parallel

    def teardown_method(self):
        import tools.web_tools
        tools.web_tools._parallel_client = None
        os.environ.pop("PARALLEL_API_KEY", None)
        sys.modules.pop("parallel", None)

    def test_creates_client_with_key(self):
        """PARALLEL_API_KEY set → creates Parallel client."""
        with patch.dict(os.environ, {"PARALLEL_API_KEY": "test-key"}):
            from tools.web_tools import _get_parallel_client
            from parallel import Parallel
            client = _get_parallel_client()
            assert client is not None
            assert isinstance(client, Parallel)

    def test_no_key_raises_with_helpful_message(self):
        """No PARALLEL_API_KEY → ValueError with guidance."""
        from tools.web_tools import _get_parallel_client
        with pytest.raises(ValueError, match="PARALLEL_API_KEY"):
            _get_parallel_client()

    def test_singleton_returns_same_instance(self):
        """Second call returns cached client."""
        with patch.dict(os.environ, {"PARALLEL_API_KEY": "test-key"}):
            from tools.web_tools import _get_parallel_client
            client1 = _get_parallel_client()
            client2 = _get_parallel_client()
            assert client1 is client2


class TestWebSearchSchema:
    """Test suite for web_search tool schema and handler wiring."""

    def test_schema_exposes_optional_limit(self):
        import tools.web_tools

        limit_schema = tools.web_tools.WEB_SEARCH_SCHEMA["parameters"]["properties"]["limit"]

        assert limit_schema["type"] == "integer"
        assert limit_schema["minimum"] == 1
        assert limit_schema["maximum"] == 100
        assert limit_schema["default"] == 5
        assert "limit" not in tools.web_tools.WEB_SEARCH_SCHEMA["parameters"]["required"]

    def test_registered_handler_passes_limit(self):
        import tools.web_tools

        entry = tools.web_tools.registry.get_entry("web_search")
        with patch("tools.web_tools.web_search_tool", return_value='{"success": true}') as mock_search:
            result = entry.handler({"query": "site:example.com docs", "limit": 12})

        assert result == '{"success": true}'
        mock_search.assert_called_once_with("site:example.com docs", limit=12)

    def test_registered_handler_defaults_limit_to_five(self):
        import tools.web_tools

        entry = tools.web_tools.registry.get_entry("web_search")
        with patch("tools.web_tools.web_search_tool", return_value='{"success": true}') as mock_search:
            result = entry.handler({"query": "docs"})

        assert result == '{"success": true}'
        mock_search.assert_called_once_with("docs", limit=5)

    def test_web_search_clamps_limit_before_backend_call(self):
        import tools.web_tools

        # After the web-provider plugin migration, _parallel_search lives in
        # plugins.web.parallel.provider.ParallelWebSearchProvider.search; the
        # tool dispatcher resolves a provider from the registry and calls
        # provider.search(query, limit). Mock the provider lookup so we can
        # assert the limit is clamped before reaching the backend.
        fake_search = MagicMock(return_value={"success": True, "data": {"web": []}})
        fake_provider = MagicMock(
            name="ParallelWebSearchProvider",
            supports_search=MagicMock(return_value=True),
        )
        fake_provider.search = fake_search
        fake_provider.name = "parallel"

        with patch("tools.web_tools._get_search_backend", return_value="parallel"), \
             patch("agent.web_search_registry.get_provider", return_value=fake_provider), \
             patch("tools.interrupt.is_interrupted", return_value=False), \
             patch.object(tools.web_tools._debug, "log_call"), \
             patch.object(tools.web_tools._debug, "save"):
            result = json.loads(tools.web_tools.web_search_tool("docs", limit=500))

        assert result == {"success": True, "data": {"web": []}}
        fake_search.assert_called_once_with("docs", 100)


class TestWebSearchErrorHandling:
    """Test suite for web_search_tool() error responses."""

    def test_search_error_response_does_not_expose_diagnostics(self):
        import tools.web_tools

        # Mock the registry's get_provider to return a fake provider whose
        # .search() raises so we can verify error sanitization.
        fake_provider = MagicMock(
            name="ParallelWebSearchProvider",
            supports_search=MagicMock(return_value=True),
        )
        fake_provider.search.side_effect = RuntimeError("boom")
        fake_provider.name = "parallel"

        with patch("tools.web_tools._get_search_backend", return_value="parallel"), \
             patch("agent.web_search_registry.get_provider", return_value=fake_provider), \
             patch("tools.interrupt.is_interrupted", return_value=False), \
             patch.object(tools.web_tools._debug, "log_call") as mock_log_call, \
             patch.object(tools.web_tools._debug, "save"):
            result = json.loads(tools.web_tools.web_search_tool("test query", limit=3))

        assert result == {"error": "Error searching web: boom"}

        debug_payload = mock_log_call.call_args.args[1]
        assert debug_payload["error"] == "Error searching web: boom"
        assert "traceback" not in debug_payload["error"]
        assert "exception_type" not in debug_payload["error"]
        assert "config" not in result
        assert "exception_type" not in result
        assert "exception_chain" not in result
        assert "traceback" not in result


class TestCheckWebApiKey:
    """Test suite for check_web_api_key() unified availability check."""

    _ENV_KEYS = (
        "EXA_API_KEY",
        "PARALLEL_API_KEY",
        "FIRECRAWL_API_KEY",
        "FIRECRAWL_API_URL",
        "FIRECRAWL_GATEWAY_URL",
        "TOOL_GATEWAY_DOMAIN",
        "TOOL_GATEWAY_SCHEME",
        "TOOL_GATEWAY_USER_TOKEN",
        "TAVILY_API_KEY",
    )

    def setup_method(self):
        for key in self._ENV_KEYS:
            os.environ.pop(key, None)
        self._managed_patchers = [
            patch("tools.web_tools.managed_nous_tools_enabled", return_value=True),
            patch("tools.managed_tool_gateway.managed_nous_tools_enabled", return_value=True),
        ]
        for p in self._managed_patchers:
            p.start()

    def teardown_method(self):
        for key in self._ENV_KEYS:
            os.environ.pop(key, None)
        for p in self._managed_patchers:
            p.stop()

    def test_parallel_key_only(self):
        with patch.dict(os.environ, {"PARALLEL_API_KEY": "test-key"}):
            from tools.web_tools import check_web_api_key
            assert check_web_api_key() is True

    @pytest.mark.parametrize(
        "env_key",
        ["EXA_API_KEY", "FIRECRAWL_API_KEY", "FIRECRAWL_API_URL", "TAVILY_API_KEY"],
    )
    def test_excluded_provider_key_only_returns_false(self, env_key):
        with patch.dict(os.environ, {env_key: "legacy-key"}), \
             patch("tools.web_tools._ddgs_package_importable", return_value=False):
            from tools.web_tools import check_web_api_key
            assert check_web_api_key() is False

    def test_no_keys_returns_false(self):
        from tools.web_tools import check_web_api_key
        with patch("tools.web_tools._ddgs_package_importable", return_value=False):
            assert check_web_api_key() is False

    def test_both_keys_returns_true(self):
        with patch.dict(os.environ, {
            "PARALLEL_API_KEY": "test-key",
            "FIRECRAWL_API_KEY": "fc-test",
        }):
            from tools.web_tools import check_web_api_key
            assert check_web_api_key() is True

    def test_parallel_with_excluded_keys_returns_true(self):
        with patch.dict(os.environ, {
            "PARALLEL_API_KEY": "test-key",
            "FIRECRAWL_API_KEY": "fc-test",
            "TAVILY_API_KEY": "tvly-test",
        }):
            from tools.web_tools import check_web_api_key
            assert check_web_api_key() is True

    def test_tool_gateway_returns_true(self):
        with patch("tools.web_tools._peek_nous_access_token", return_value="nous-token"), \
             patch("tools.web_tools._ddgs_package_importable", return_value=False):
            from tools.web_tools import check_web_api_key
            assert check_web_api_key() is False

    def test_tool_gateway_availability_skips_refresh_for_expired_cached_token(
        self,
        tmp_path,
        monkeypatch,
    ):
        monkeypatch.delenv("TOOL_GATEWAY_USER_TOKEN", raising=False)
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        expired_at = "2000-01-01T00:00:00+00:00"
        (tmp_path / "auth.json").write_text(json.dumps({
            "providers": {
                "nous": {
                    "access_token": "expired-token",
                    "refresh_token": "refresh-token",
                    "expires_at": expired_at,
                }
            }
        }))
        refresh_calls = []

        def _record_refresh(*, refresh_skew_seconds=120, **_kwargs):
            refresh_calls.append(refresh_skew_seconds)
            return "fresh-token"

        monkeypatch.setattr(
            "hermes_cli.auth.resolve_nous_access_token",
            _record_refresh,
        )

        with patch.dict(
            os.environ,
            {"FIRECRAWL_GATEWAY_URL": "http://127.0.0.1:3002"},
            clear=False,
        ):
            from tools.web_tools import check_web_api_key

            assert check_web_api_key() is False

        assert refresh_calls == []

    def test_configured_backend_must_match_available_provider(self):
        with patch("tools.web_tools._load_web_config", return_value={"backend": "parallel"}):
            with patch("tools.web_tools._read_nous_access_token", return_value="nous-token"):
                with patch.dict(os.environ, {"FIRECRAWL_GATEWAY_URL": "http://127.0.0.1:3002"}, clear=False):
                    from tools.web_tools import check_web_api_key
                    assert check_web_api_key() is False

    def test_configured_firecrawl_backend_rejected(self):
        with patch("tools.web_tools._load_web_config", return_value={"backend": "firecrawl"}):
            with patch("tools.web_tools._peek_nous_access_token", return_value="nous-token"):
                with patch.dict(os.environ, {"FIRECRAWL_GATEWAY_URL": "http://127.0.0.1:3002"}, clear=False):
                    from tools.web_tools import check_web_api_key
                    assert check_web_api_key() is False


def test_web_requires_env_excludes_removed_provider_keys():
    from tools.web_tools import _web_requires_env

    env = _web_requires_env()
    assert "PARALLEL_API_KEY" in env
    assert "SEARXNG_URL" in env
    assert "BRAVE_SEARCH_API_KEY" in env
    assert "XAI_API_KEY" in env
    assert "EXA_API_KEY" not in env
    assert "TAVILY_API_KEY" not in env
    assert "FIRECRAWL_API_KEY" not in env
