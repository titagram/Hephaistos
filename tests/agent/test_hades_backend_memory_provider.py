from __future__ import annotations

import json


def _create_linked_provider(monkeypatch, tmp_path, *, items=None):
    from hermes_cli import hades_backend_db as db
    from hermes_cli.hades_backend_runtime import workspace_fingerprint
    from plugins.memory import load_memory_provider

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    fp = workspace_fingerprint(workspace, "proj_1")
    with db.connect_closing() as conn:
        db.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"memory": True},
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
        if items is not None:
            db.replace_memory_cache(
                conn,
                project_id="proj_1",
                workspace_binding_id="wb_1",
                version="v1",
                items=items,
            )

    provider = load_memory_provider("hades_backend")
    assert provider is not None
    provider.initialize("session_1", hermes_home=str(tmp_path / "home"), platform="cli")
    return provider


def test_hades_backend_memory_provider_prefetches_linked_project_cache(monkeypatch, tmp_path):
    from hermes_cli import hades_backend_db as db
    from hermes_cli.hades_backend_runtime import workspace_fingerprint
    from plugins.memory import load_memory_provider

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    fp = workspace_fingerprint(workspace, "proj_1")
    with db.connect_closing() as conn:
        db.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"memory": True},
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
        db.replace_memory_cache(
            conn,
            project_id="proj_1",
            workspace_binding_id="wb_1",
            version="v1",
            items=[
                {
                    "id": "mem_1",
                    "domain": "project_memory",
                    "summary": "The Laravel API uses /api/hades/v1 routes.",
                    "etag": "e1",
                },
                {
                    "id": "chunk_1",
                    "domain": "source_chunks",
                    "schema": "hades.backend_wiki.file_chunk.v1",
                    "summary": "RAW route dump should not be auto injected.",
                }
            ],
        )

    provider = load_memory_provider("hades_backend")
    assert provider is not None
    provider.initialize("session_1", hermes_home=str(tmp_path / "home"), platform="cli")

    context = provider.prefetch("Which backend routes should I use?", session_id="session_1")

    assert "Shared Hades project memory" in context
    assert "/api/hades/v1" in context
    assert "RAW route dump" not in context
    assert [schema["name"] for schema in provider.get_tool_schemas()] == [
        "hades_backend_project_memory_search"
    ]


def test_hades_backend_memory_provider_prefetch_ranks_by_query(monkeypatch, tmp_path):
    provider = _create_linked_provider(
        monkeypatch,
        tmp_path,
        items=[
            {"id": "mem_1", "domain": "project_memory", "summary": "Security billing jobs use route exports."},
            {
                "id": "mem_2",
                "domain": "wiki",
                "summary": "Security activity routes are handled by SecurityActivityCategoryController.",
            },
        ],
    )

    context = provider.prefetch("security activity routes", session_id="session_1")

    assert context.index("Security activity routes") < context.index("Security billing jobs")


def test_hades_backend_memory_search_tool_filters_domains_and_raw_chunks(monkeypatch, tmp_path):
    provider = _create_linked_provider(
        monkeypatch,
        tmp_path,
        items=[
            {"id": "mem_1", "domain": "logbook", "summary": "DECIDED: backend memory stays authoritative."},
            {"id": "mem_2", "domain": "wiki", "summary": "Backend routes live under /api/hades/v1."},
            {
                "id": "chunk_1",
                "domain": "source_chunks",
                "schema": "hades.backend_wiki.file_chunk.v1",
                "path": "docs/backend.md",
                "summary": "Exact chunk mentions /api/hades/v1/source.",
            },
        ],
    )

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_project_memory_search",
            {"query": "backend routes", "domain": "wiki", "limit": 5},
        )
    )

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert result["domain"] == "wiki"
    assert result["count"] == 1
    assert result["raw_chunks_omitted"] == 0
    assert result["items"][0]["id"] == "mem_2"
    assert result["items"][0]["domain"] == "wiki"


def test_hades_backend_memory_search_tool_prefers_live_backend(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)

    class FakeClient:
        def __init__(self):
            self.calls = []
            self.closed = 0

        def memory_search(self, **payload):
            self.calls.append(payload)
            return {
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "version": "search_v1",
                "etag": "search_v1",
                "query": payload["query"],
                "domain": payload["domain"],
                "include_raw_chunks": payload["include_raw_chunks"],
                "count": 1,
                "candidate_count": 2,
                "truncated": True,
                "raw_chunks_omitted": 1,
                "freshness": {
                    "workspace_head_commit": "abc123",
                    "index_status": "live_query",
                },
                "server_time": "2026-07-06T12:00:00Z",
                "items": [
                    {
                        "id": "wiki_1",
                        "domain": "wiki",
                        "schema": "devboard.wiki_revision.v1",
                        "source": "wiki_revision",
                        "summary": "Live backend wiki says Hades routes live under /api/hades/v1.",
                        "score": 18,
                        "page_slug": "architecture/hades-memory",
                        "raw_chunk": False,
                    }
                ],
            }

        def close(self):
            self.closed += 1

    fake = FakeClient()
    import plugins.memory.hades_backend as hades_memory

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", lambda *, timeout=None: fake)

    context = provider.prefetch("Hades routes", session_id="session_1")
    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_project_memory_search",
            {"query": "Hades routes", "domain": "wiki", "limit": 5},
        )
    )

    assert "live search search_v1" in context
    assert "/api/hades/v1" in context
    assert result["status"] == "ok"
    assert result["searched_cache_only"] is False
    assert result["backend_version"] == "search_v1"
    assert result["candidate_count"] == 2
    assert result["truncated"] is True
    assert result["raw_chunks_omitted"] == 1
    assert result["freshness"]["index_status"] == "live_query"
    assert result["items"][0]["page_slug"] == "architecture/hades-memory"
    assert fake.calls[0]["limit"] == 8
    assert fake.calls[1]["limit"] == 5
    assert fake.calls[1]["workspace_binding_id"] == "wb_1"
    assert fake.closed == 2


def test_hades_backend_memory_live_search_uses_short_timeout(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)
    timeouts = []

    class FakeClient:
        def memory_search(self, **payload):
            return {
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "version": "search_v1",
                "query": payload["query"],
                "domain": payload["domain"],
                "count": 0,
                "candidate_count": 0,
                "items": [],
            }

        def close(self):
            pass

    import plugins.memory.hades_backend as hades_memory

    def client_from_config(*, timeout=None):
        timeouts.append(timeout)
        return FakeClient()

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", client_from_config)

    provider.handle_tool_call(
        "hades_backend_project_memory_search",
        {"query": "Hades routes"},
    )

    assert timeouts == [2.0]


def test_hades_backend_memory_search_tool_allows_artifacts_domain(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)

    class FakeClient:
        def memory_search(self, **payload):
            return {
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "version": "search_artifacts",
                "query": payload["query"],
                "domain": payload["domain"],
                "count": 1,
                "candidate_count": 1,
                "raw_chunks_omitted": 0,
                "items": [
                    {
                        "id": "artifact_1",
                        "domain": "artifacts",
                        "schema": "hades.git_tree.v1",
                        "source": "hades.git_tree.v1",
                        "summary": "Project index: GET /hades/memory -> MemoryController@index",
                        "score": 16,
                    }
                ],
            }

        def close(self):
            pass

    import plugins.memory.hades_backend as hades_memory

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", lambda *, timeout=None: FakeClient())

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_project_memory_search",
            {"query": "hades memory route", "domain": "artifacts"},
        )
    )

    assert result["status"] == "ok"
    assert result["domain"] == "artifacts"
    assert result["items"][0]["domain"] == "artifacts"
    assert result["items"][0]["schema"] == "hades.git_tree.v1"


def test_hades_backend_memory_search_tool_can_include_raw_chunks(monkeypatch, tmp_path):
    provider = _create_linked_provider(
        monkeypatch,
        tmp_path,
        items=[
            {
                "id": "chunk_1",
                "domain": "source_chunks",
                "schema": "hades.backend_wiki.file_chunk.v1",
                "path": "docs/backend.md",
                "summary": "Exact chunk mentions taxonomy route extraction.",
            },
        ],
    )

    without_raw = json.loads(
        provider.handle_tool_call(
            "hades_backend_project_memory_search",
            {"query": "taxonomy route", "domain": "source_chunks"},
        )
    )
    with_raw = json.loads(
        provider.handle_tool_call(
            "hades_backend_project_memory_search",
            {
                "query": "taxonomy route",
                "domain": "source_chunks",
                "include_raw_chunks": True,
            },
        )
    )

    assert without_raw["count"] == 0
    assert without_raw["raw_chunks_omitted"] == 1
    assert with_raw["count"] == 1
    assert with_raw["items"][0]["raw_chunk"] is True
    assert with_raw["items"][0]["source"] == "docs/backend.md"


def test_hades_backend_memory_search_tool_reports_unmapped_project(monkeypatch, tmp_path):
    from hermes_cli import hades_backend_db as db
    from plugins.memory import load_memory_provider

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "unmapped"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    with db.connect_closing() as conn:
        db.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"memory": True},
        )

    provider = load_memory_provider("hades_backend")
    assert provider is not None
    provider.initialize("session_1", hermes_home=str(tmp_path / "home"), platform="cli")

    block = provider.system_prompt_block()
    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_project_memory_search",
            {"query": "routes"},
        )
    )

    assert "not linked" in block
    assert result["status"] == "unmapped_project"
    assert result["items"] == []


def test_hades_backend_memory_write_creates_local_proposal(monkeypatch, tmp_path):
    from hermes_cli import hades_backend_db as db
    from hermes_cli.hades_backend_runtime import workspace_fingerprint
    from plugins.memory import load_memory_provider

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    fp = workspace_fingerprint(workspace, "proj_1")
    with db.connect_closing() as conn:
        db.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"memory": True},
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

    provider = load_memory_provider("hades_backend")
    assert provider is not None
    provider.initialize("session_1", hermes_home=str(tmp_path / "home"), platform="cli")
    provider.on_memory_write("add", "project", "Keep backend responses bounded", metadata={"source": "test"})

    with db.connect_closing() as conn:
        proposals = db.list_memory_proposals(conn)

    assert len(proposals) == 1
    assert proposals[0].action == "create"
    assert proposals[0].intent == "memory_write"
    assert "bounded" in proposals[0].summary


def test_hades_backend_memory_write_preserves_update_identity(monkeypatch, tmp_path):
    from hermes_cli import hades_backend_db as db
    from hermes_cli.hades_backend_runtime import workspace_fingerprint
    from plugins.memory import load_memory_provider

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    fp = workspace_fingerprint(workspace, "proj_1")
    with db.connect_closing() as conn:
        db.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"memory": True},
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

    provider = load_memory_provider("hades_backend")
    assert provider is not None
    provider.initialize("session_1", hermes_home=str(tmp_path / "home"), platform="cli")
    provider.on_memory_write(
        "replace",
        "project",
        "Use bounded backend responses",
        metadata={"old_text": "Use verbose backend responses", "memory_id": "mem_1", "etag": "etag_1"},
    )

    with db.connect_closing() as conn:
        proposals = db.list_memory_proposals(conn)

    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.action == "update"
    assert proposal.summary == "Use bounded backend responses"
    assert proposal.provenance["memory_id"] == "mem_1"
    assert proposal.provenance["base_version"] == "etag_1"
    assert proposal.provenance["previous_summary"] == "Use verbose backend responses"


def test_hades_backend_memory_write_creates_delete_proposal(monkeypatch, tmp_path):
    from hermes_cli import hades_backend_db as db
    from hermes_cli.hades_backend_runtime import workspace_fingerprint
    from plugins.memory import load_memory_provider

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    fp = workspace_fingerprint(workspace, "proj_1")
    with db.connect_closing() as conn:
        db.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"memory": True},
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

    provider = load_memory_provider("hades_backend")
    assert provider is not None
    provider.initialize("session_1", hermes_home=str(tmp_path / "home"), platform="cli")
    provider.on_memory_write(
        "remove",
        "project",
        "",
        metadata={"old_text": "Obsolete backend route", "memory_id": "mem_2", "base_version": "v2"},
    )

    with db.connect_closing() as conn:
        proposals = db.list_memory_proposals(conn)

    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.action == "delete"
    assert proposal.summary == "Obsolete backend route"
    assert proposal.provenance["memory_id"] == "mem_2"
    assert proposal.provenance["base_version"] == "v2"
    assert proposal.provenance["previous_summary"] == "Obsolete backend route"
