from __future__ import annotations

import json
import copy
from types import SimpleNamespace

import pytest


def test_legacy_hades_backend_provider_never_syncs_after_normal_turn(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    from agent.memory_manager import MemoryManager
    from hermes_cli import hades_backend_db as db
    from hermes_cli.config import load_config, save_config
    from hermes_cli.hades_backend_runtime import workspace_fingerprint
    from plugins.memory import load_memory_provider
    import plugins.memory.hades_backend as provider_mod

    save_config({"memory": {"provider": "hades_backend"}})

    fp = workspace_fingerprint(workspace, "proj_1")
    with db.connect_closing() as conn:
        db.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"memory": True, "jobs": True},
        )
        db.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_1",
            workspace_fingerprint=fp,
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="wb_1",
        )

    calls = []
    def fail_backend_sync(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("normal turn attempted automatic Backend sync")

    monkeypatch.setattr(
        provider_mod,
        "run_backend_sync",
        fail_backend_sync,
        raising=False,
    )

    provider = load_memory_provider(load_config()["memory"]["provider"])
    manager = MemoryManager()
    manager.add_provider(provider)
    manager.initialize_all(
        session_id="session_1",
        hermes_home=str(tmp_path / "home"),
        platform="cli",
    )
    manager.sync_all("user", "assistant", session_id="session_1")
    manager.flush_pending(timeout=5)

    assert calls == []


def test_hades_backend_memory_provider_uses_binding_for_current_workspace_not_default_agent(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    current_workspace = tmp_path / "current"
    default_workspace = tmp_path / "default"
    current_workspace.mkdir()
    default_workspace.mkdir()
    monkeypatch.chdir(current_workspace)

    from hermes_cli import hades_backend_db as db
    import plugins.memory.hades_backend as provider_mod

    with db.connect_closing() as conn:
        db.save_agent(
            conn,
            agent_id="agent_current_workspace",
            project_id="project_current_workspace",
            base_url="https://backend.example",
            label="current-workspace",
            token_env_key="TOKEN_CURRENT_WORKSPACE",
            capabilities={"memory": True},
        )
        db.upsert_workspace_binding(
            conn,
            project_id="project_current_workspace",
            agent_id="agent_current_workspace",
            local_project_id="local_current_workspace",
            workspace_fingerprint="fingerprint_current_workspace",
            display_path=str(current_workspace),
            repo_root=str(current_workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="binding_current_workspace",
        )
        db.save_agent(
            conn,
            agent_id="agent_newer_default",
            project_id="project_newer_default",
            base_url="https://backend.example",
            label="newer-default",
            token_env_key="TOKEN_NEWER_DEFAULT",
            capabilities={"memory": True},
        )
        db.upsert_workspace_binding(
            conn,
            project_id="project_newer_default",
            agent_id="agent_newer_default",
            local_project_id="local_newer_default",
            workspace_fingerprint="fingerprint_newer_default",
            display_path=str(default_workspace),
            repo_root=str(default_workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="binding_newer_default",
        )

    provider = provider_mod.HadesBackendMemoryProvider()
    provider.initialize("session_1", hermes_home=str(tmp_path / "home"), platform="cli")

    assert provider._binding is not None
    assert provider._binding.backend_workspace_binding_id == "binding_current_workspace"


def test_hades_backend_memory_provider_initializes_current_owned_binding_over_historical_binding(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "repo"
    nested = workspace / "packages" / "current"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    from hermes_cli import hades_backend_db as db
    import plugins.memory.hades_backend as provider_mod

    monkeypatch.setattr(db, "_now", lambda: 1000)
    with db.connect_closing() as conn:
        db.save_agent(
            conn,
            agent_id="agent_historical",
            project_id="project_historical",
            base_url="https://backend.example",
            label="historical",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_HISTORICAL",
            capabilities={"memory": True},
        )
        db.save_agent(
            conn,
            agent_id="agent_current",
            project_id="project_current",
            base_url="https://backend.example",
            label="current",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_CURRENT",
            capabilities={"memory": True},
        )
        db.upsert_workspace_binding(
            conn,
            project_id="project_current",
            agent_id="agent_current",
            local_project_id="local_current",
            workspace_fingerprint="fingerprint_current",
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="binding_current",
        )
        # Inserted later in the same second and rooted more specifically: both
        # old selection tie-breakers favored this historical identity.
        db.upsert_workspace_binding(
            conn,
            project_id="project_historical",
            agent_id="agent_historical",
            local_project_id="local_historical",
            workspace_fingerprint="fingerprint_historical",
            display_path="~/repo/packages",
            repo_root=str(workspace / "packages"),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="binding_historical",
        )

    provider = provider_mod.HadesBackendMemoryProvider()
    provider.initialize("session_1", hermes_home=str(tmp_path / "home"), platform="cli")

    assert provider._binding is not None
    assert provider._binding.project_id == "project_current"
    assert provider._binding.agent_id == "agent_current"
    assert provider._binding.backend_workspace_binding_id == "binding_current"


@pytest.mark.parametrize("recall_path", ["prefetch", "search_tool"])
def test_hades_backend_memory_provider_refreshes_rebound_workspace_before_recall(
    monkeypatch, tmp_path, recall_path
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    from hermes_cli import hades_backend_db as db
    import hermes_cli.hades_backend_runtime as runtime
    import plugins.memory.hades_backend as provider_mod

    clock = iter(range(2_000_000_000, 2_000_000_100))
    monkeypatch.setattr(db, "_now", lambda: next(clock))

    with db.connect_closing() as conn:
        db.save_agent(
            conn,
            agent_id="agent_a",
            project_id="project_a",
            base_url="https://backend.example",
            label="a",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_A",
            capabilities={"memory": True},
        )
        binding_a = db.upsert_workspace_binding(
            conn,
            project_id="project_a",
            agent_id="agent_a",
            local_project_id="local_a",
            workspace_fingerprint="fingerprint_a",
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="binding_a",
        )

    provider = provider_mod.HadesBackendMemoryProvider()
    provider.initialize("session_1", hermes_home=str(tmp_path / "home"), platform="cli")
    assert provider._binding == binding_a

    with db.connect_closing() as conn:
        db.save_agent(
            conn,
            agent_id="agent_b",
            project_id="project_b",
            base_url="https://backend.example",
            label="b",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_B",
            capabilities={"memory": True},
        )
        db.upsert_workspace_binding(
            conn,
            project_id="project_b",
            agent_id="agent_b",
            local_project_id="local_b",
            workspace_fingerprint="fingerprint_b",
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="binding_b",
        )

    search_requests = []
    sync_calls = []

    class FakeClient:
        def memory_search(self, **payload):
            search_requests.append(payload)
            return {
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "items": [
                    {
                        "id": "memory_b",
                        "domain": "project_memory",
                        "summary": "Project B fact",
                    }
                ],
            }

        def close(self):
            pass

    def fail_backend_sync(*args, **kwargs):
        sync_calls.append((args, kwargs))
        raise AssertionError("binding refresh attempted Backend sync")

    monkeypatch.setattr(runtime, "client_from_config", lambda **kwargs: FakeClient())
    monkeypatch.setattr(
        provider_mod,
        "run_backend_sync",
        fail_backend_sync,
        raising=False,
    )

    if recall_path == "prefetch":
        assert "Project B fact" in provider.prefetch("project fact")
    else:
        result = json.loads(
            provider.handle_tool_call(
                provider_mod.SEARCH_TOOL_NAME,
                {"query": "project fact"},
            )
        )
        assert result["items"][0]["summary"] == "Project B fact"

    assert search_requests == [
        {
            "project_id": "project_b",
            "workspace_binding_id": "binding_b",
            "query": "project fact",
            "domain": "all",
            "limit": 8,
            "include_raw_chunks": False,
        }
    ]
    assert sync_calls == []


def test_graph_v2_local_artifact_selection_requires_exact_active_identity():
    import plugins.memory.hades_backend as provider_mod
    from tests.hermes_cli.test_hades_graph_contract import _valid_flow_artifact

    exact_graph = _valid_flow_artifact()
    source_identity = exact_graph["source"]
    active = {
        "schema": "hades.code_graph.v2",
        "project_id": exact_graph["project"]["project_id"],
        "workspace_binding_id": exact_graph["project"]["workspace_binding_id"],
        "source_identity": source_identity,
        "artifact_graph_version": exact_graph["graph_contract"]["artifact_graph_version"],
        "projection_version": "c" * 64,
        "publication_status": "ready",
    }

    sources = [
        {
            "origin": "legacy",
            "item": {
                "id": "legacy",
                "schema": "hades.code_graph.v1",
                "payload": {"schema": "hades.code_graph.v1", "symbols": [], "edges": []},
            },
        },
        {
            "origin": "stale-v2",
            "item": {
                "id": "stale",
                "schema": "hades.code_graph.v2",
                "projection_version": "e" * 64,
                "payload": copy.deepcopy(exact_graph),
            },
        },
        {
            "origin": "active-v2",
            "item": {
                "id": "active",
                "schema": "hades.code_graph.v2",
                "projection_version": "c" * 64,
                "payload": copy.deepcopy(exact_graph),
            },
        },
    ]

    selected = provider_mod._local_graph_artifacts(
        sources, active_graph_identity=active
    )

    assert provider_mod.GRAPH_ARTIFACT_SCHEMAS == {
        "hades.code_graph.v2",
        "hades.organism_graph.v1",
    }
    assert len(selected) == 1
    assert selected[0]["artifact_id"] == "active"
    assert selected[0]["artifact"]["graph_contract"]["artifact_graph_version"] == active[
        "artifact_graph_version"
    ]
    assert provider_mod._local_graph_artifacts(sources) == []


def test_graph_search_resolves_vector_candidate_with_exact_topology_query():
    import plugins.memory.hades_backend as provider_mod

    provider = object.__new__(provider_mod.HadesBackendMemoryProvider)
    provider._binding = SimpleNamespace(
        project_id="project-1",
        backend_workspace_binding_id="binding-1",
    )
    provider._active_graph_identity = lambda _scope: {
        "schema": "hades.code_graph.v2",
        "project_id": "project-1",
        "workspace_binding_id": "binding-1",
        "projection_version": "c" * 64,
        "publication_status": "ready",
    }
    traverse_calls = []
    provider._backend_memory_search = lambda **_kwargs: (
        {
            "project_id": "project-1",
            "workspace_binding_id": "binding-1",
            "domain": "artifacts",
            "items": [
                {
                    "id": "vector-hit",
                    "kind": "vector_candidate",
                    "summary": "Order handler candidate",
                    "graph_handle": "hades:node:v2:order-handler",
                    "score": 91,
                }
            ],
        },
        None,
    )

    def topology(**payload):
        traverse_calls.append(payload)
        return (
            {
                "project_id": "project-1",
                "workspace_binding_id": "binding-1",
                "schema": "hades.code_graph.v2",
                "projection_version": "c" * 64,
                "coverage": {"records": {"nodes": 1, "edges": 0}},
                "start": payload["start"],
                "direction": payload["direction"],
                "max_depth": payload["max_depth"],
                "limit": payload["limit"],
                "nodes": [
                    {
                        "id": "hades:node:v2:order-handler",
                        "kind": "function",
                        "label": "OrderHandler",
                    }
                ],
                "edges": [],
            },
            None,
        )

    provider._backend_graph_traverse = topology

    result = json.loads(provider._handle_graph_search({"query": "order", "limit": 5}))

    assert result["topology_resolved"] is True
    assert result["schema"] == "hades.code_graph.v2"
    assert result["projection_version"] == "c" * 64
    assert result["coverage"] == {"records": {"nodes": 1, "edges": 0}}
    assert result["nodes"][0]["id"] == "hades:node:v2:order-handler"
    assert result["vector_candidate_handles"] == ["hades:node:v2:order-handler"]
    assert traverse_calls == [
        {
            "start": "hades:node:v2:order-handler",
            "direction": "any",
            "max_depth": 1,
            "limit": 5,
            "scope": "project",
        }
    ]


def test_graph_search_checks_every_vector_handle_against_authoritative_v2_identity():
    import plugins.memory.hades_backend as provider_mod

    provider = object.__new__(provider_mod.HadesBackendMemoryProvider)
    provider._binding = SimpleNamespace(
        project_id="project-1",
        backend_workspace_binding_id="binding-1",
    )
    provider._active_graph_identity = lambda _scope: {
        "schema": "hades.code_graph.v2",
        "project_id": "project-1",
        "workspace_binding_id": "binding-1",
        "projection_version": "c" * 64,
        "publication_status": "ready",
    }
    handles = [
        "hades:node:v2:good",
        "hades:node:v2:wrong-schema",
        "hades:node:v2:wrong-project",
        "hades:node:v2:wrong-binding",
        "hades:node:v2:wrong-projection",
        "hades:node:v2:missing-coverage",
    ]
    provider._backend_memory_search = lambda **_kwargs: (
        {
            "project_id": "project-1",
            "workspace_binding_id": "binding-1",
            "domain": "artifacts",
            "items": [
                {
                    "id": f"vector-{index}",
                    "kind": "vector_candidate",
                    "graph_handle": handle,
                    "score": 90 - index,
                }
                for index, handle in enumerate(handles)
            ],
        },
        None,
    )
    traverse_calls = []

    def topology(**payload):
        traverse_calls.append(payload)
        handle = payload["start"]
        response = {
            "project_id": "project-1",
            "workspace_binding_id": "binding-1",
            "schema": "hades.code_graph.v2",
            "projection_version": "c" * 64,
            "coverage": {"records": {"nodes": 1, "edges": 0}},
            "start": handle,
            "direction": payload["direction"],
            "max_depth": payload["max_depth"],
            "limit": payload["limit"],
            "nodes": [{"id": handle, "kind": "function", "label": handle}],
            "edges": [],
        }
        if handle.endswith("wrong-schema"):
            response["schema"] = "hades.code_graph.v1"
        elif handle.endswith("wrong-project"):
            response["project_id"] = "project-2"
        elif handle.endswith("wrong-binding"):
            response["workspace_binding_id"] = "binding-2"
        elif handle.endswith("wrong-projection"):
            response["projection_version"] = "d" * 64
        elif handle.endswith("missing-coverage"):
            response.pop("coverage")
        return response, None

    provider._backend_graph_traverse = topology

    result = json.loads(provider._handle_graph_search({"query": "order", "limit": 8}))

    assert [call["start"] for call in traverse_calls] == handles
    assert result["topology_resolved"] is False
    assert result["topology_partial"] is True
    assert result["topology_resolved_handles"] == ["hades:node:v2:good"]
    assert result["topology_unresolved_handles"] == handles[1:]
    assert result["vector_candidate_handles"] == handles
    assert [node["id"] for node in result["nodes"]] == ["hades:node:v2:good"]
    assert set(result["backend_topology_errors"]) == set(handles[1:])


def test_vector_candidate_without_graph_query_remains_hint_not_topology():
    import plugins.memory.hades_backend as provider_mod

    provider = object.__new__(provider_mod.HadesBackendMemoryProvider)
    provider._binding = SimpleNamespace(
        project_id="project-1",
        backend_workspace_binding_id="binding-1",
    )
    provider._backend_memory_search = lambda **_kwargs: (
        {
            "domain": "artifacts",
            "items": [
                {
                    "id": "vector-hit",
                    "kind": "vector_candidate",
                    "summary": "Unresolved order candidate",
                    "graph_handle": "hades:node:v2:missing",
                    "score": 80,
                }
            ],
        },
        None,
    )
    provider._backend_graph_traverse = lambda **_kwargs: (None, "graph query unavailable")

    result = json.loads(provider._handle_graph_search({"query": "order", "limit": 5}))

    assert result["topology_resolved"] is False
    assert result["vector_candidate_handles"] == ["hades:node:v2:missing"]
    assert result["backend_topology_error"] == "graph query unavailable"
    assert "nodes" not in result
    assert "edges" not in result
