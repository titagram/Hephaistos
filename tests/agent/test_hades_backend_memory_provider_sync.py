from __future__ import annotations

import json
import copy
from types import SimpleNamespace


def test_hades_backend_memory_provider_piggybacks_sync_once_per_interval(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    from hermes_cli import hades_backend_db as db
    from hermes_cli.hades_backend_runtime import workspace_fingerprint
    import plugins.memory.hades_backend as provider_mod

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
    monkeypatch.setattr(provider_mod, "run_backend_sync", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(provider_mod.time, "time", lambda: 1000)

    provider = provider_mod.HadesBackendMemoryProvider()
    provider.initialize("session_1", hermes_home=str(tmp_path / "home"), platform="cli")

    provider.sync_turn("user", "assistant", session_id="session_1")
    provider.sync_turn("user again", "assistant again", session_id="session_1")

    assert len(calls) == 1
    assert calls[0] == {
        "quiet": True,
        "project_id": "proj_1",
        "workspace_binding_ids": ["wb_1"],
    }


def test_hades_backend_memory_provider_does_not_sync_without_binding(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "unlinked"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    import plugins.memory.hades_backend as provider_mod

    calls = []
    monkeypatch.setattr(provider_mod, "run_backend_sync", lambda **kwargs: calls.append(kwargs))

    provider = provider_mod.HadesBackendMemoryProvider()
    provider.initialize("session_1", hermes_home=str(tmp_path / "home"), platform="cli")
    provider.sync_turn("user", "assistant", session_id="session_1")

    assert calls == []


def test_hades_backend_memory_provider_ignores_newer_more_specific_historical_binding(
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

    calls = []
    monkeypatch.setattr(provider_mod, "run_backend_sync", lambda **kwargs: calls.append(kwargs))

    provider = provider_mod.HadesBackendMemoryProvider()
    provider.initialize("session_1", hermes_home=str(tmp_path / "home"), platform="cli")
    provider.sync_turn("user", "assistant", session_id="session_1")

    assert calls == [
        {
            "quiet": True,
            "project_id": "project_current",
            "workspace_binding_ids": ["binding_current"],
        }
    ]


def test_hades_backend_memory_provider_revalidates_binding_when_default_agent_changes(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    from hermes_cli import hades_backend_db as db
    import plugins.memory.hades_backend as provider_mod

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

    calls = []
    monkeypatch.setattr(provider_mod, "run_backend_sync", lambda **kwargs: calls.append(kwargs))
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
        binding_b = db.upsert_workspace_binding(
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

    provider.sync_turn("user", "assistant", session_id="session_1")

    assert provider._binding == binding_b
    assert calls == [
        {
            "quiet": True,
            "project_id": "project_b",
            "workspace_binding_ids": ["binding_b"],
        }
    ]


def test_hades_backend_memory_provider_skips_sync_when_cached_binding_is_unlinked(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    from hermes_cli import hades_backend_db as db
    import plugins.memory.hades_backend as provider_mod

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
        db.upsert_workspace_binding(
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

    calls = []
    monkeypatch.setattr(provider_mod, "run_backend_sync", lambda **kwargs: calls.append(kwargs))
    provider = provider_mod.HadesBackendMemoryProvider()
    provider.initialize("session_1", hermes_home=str(tmp_path / "home"), platform="cli")

    with db.connect_closing() as conn:
        db.mark_binding_unlinked(conn, "fingerprint_a")

    provider.sync_turn("user", "assistant", session_id="session_1")

    assert provider._binding is None
    assert calls == []


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
