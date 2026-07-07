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


def _php_graph_artifact():
    return {
        "schema": "hades.php_graph.v1",
        "head_commit": "abc123",
        "workspace_head_commit": "abc123",
        "routes": [
            {
                "method": "GET",
                "uri": "/orders/{order}",
                "name": "orders.show",
                "handler": "OrderController@show",
                "path": "routes/web.php",
                "line": 4,
            },
            {
                "method": "GET",
                "uri": "/invoices",
                "name": "invoices.index",
                "handler": "InvoiceController@index",
                "resource": "invoices",
                "resource_action": "index",
                "middleware": ["auth"],
                "path": "routes/web.php",
                "line": 5,
            }
        ],
        "symbols": [
            {
                "kind": "method",
                "name": "OrderController@show",
                "class": "App\\Http\\Controllers\\OrderController",
                "method": "show",
                "role": "controller",
                "path": "app/Http/Controllers/OrderController.php",
                "line": 41,
            },
            {
                "kind": "blade_view",
                "name": "view:orders.show",
                "view": "orders.show",
                "role": "blade_view",
                "path": "resources/views/orders/show.blade.php",
                "line": 1,
            },
            {
                "kind": "blade_view",
                "name": "view:layouts.app",
                "view": "layouts.app",
                "role": "blade_view",
                "path": "resources/views/layouts/app.blade.php",
                "line": 1,
            },
            {
                "kind": "blade_component",
                "name": "component:alert",
                "component": "alert",
                "role": "blade_component",
                "path": "resources/views/components/alert.blade.php",
                "line": 1,
            },
            {
                "kind": "class",
                "name": "App\\Http\\Resources\\OrderResource",
                "short_name": "OrderResource",
                "role": "api_resource",
                "path": "app/Http/Resources/OrderResource.php",
                "line": 4,
                "extends": "Illuminate\\Http\\Resources\\Json\\JsonResource",
            },
        ],
        "edges": [
            {
                "kind": "route_handler",
                "from": "route:orders.show",
                "to": "OrderController@show",
                "path": "routes/web.php",
                "line": 4,
            },
            {
                "kind": "route_handler",
                "from": "route:invoices.index",
                "to": "InvoiceController@index",
                "method": "GET",
                "uri": "/invoices",
                "path": "routes/web.php",
                "line": 5,
            },
            {
                "kind": "route_model_binding",
                "from": "route:orders.show",
                "to": "App\\Models\\Order",
                "handler": "OrderController@show",
                "param": "order",
                "table": "orders",
                "method": "GET",
                "uri": "/orders/{order}",
                "path": "routes/web.php",
                "line": 4,
            },
            {
                "kind": "route_model_table",
                "from": "route:orders.show",
                "to": "table:orders",
                "handler": "OrderController@show",
                "param": "order",
                "model": "App\\Models\\Order",
                "method": "GET",
                "uri": "/orders/{order}",
                "path": "routes/web.php",
                "line": 4,
            },
            {
                "kind": "route_uses_form_request",
                "from": "route:orders.show",
                "to": "App\\Http\\Requests\\StoreOrderRequest",
                "handler": "OrderController@show",
                "param": "request",
                "method": "GET",
                "uri": "/orders/{order}",
                "path": "routes/web.php",
                "line": 4,
            },
            {
                "kind": "route_request_validation",
                "from": "route:orders.show",
                "to": "validation:customer_id",
                "request_class": "App\\Http\\Requests\\StoreOrderRequest",
                "validation_rules": ["required", "integer", "exists"],
                "validation_path": "app/Http/Requests/StoreOrderRequest.php",
                "validation_line": 6,
                "handler": "OrderController@show",
                "param": "request",
                "method": "GET",
                "uri": "/orders/{order}",
                "path": "routes/web.php",
                "line": 4,
            },
            {
                "kind": "route_authorization",
                "from": "route:orders.show",
                "to": "ability:view",
                "handler": "OrderController@show",
                "ability": "view",
                "source": "this_authorize",
                "target_param": "order",
                "target_model": "App\\Models\\Order",
                "table": "orders",
                "method": "GET",
                "uri": "/orders/{order}",
                "path": "routes/web.php",
                "line": 4,
                "source_path": "app/Http/Controllers/OrderController.php",
                "source_line": 13,
            },
            {
                "kind": "route_authorization_table",
                "from": "route:orders.show",
                "to": "table:orders",
                "handler": "OrderController@show",
                "ability": "view",
                "source": "this_authorize",
                "target_param": "order",
                "target_model": "App\\Models\\Order",
                "table": "orders",
                "method": "GET",
                "uri": "/orders/{order}",
                "path": "routes/web.php",
                "line": 4,
                "source_path": "app/Http/Controllers/OrderController.php",
                "source_line": 13,
            },
            {
                "kind": "view_ref",
                "from": "OrderController@show",
                "to": "view:orders.show",
                "path": "app/Http/Controllers/OrderController.php",
                "line": 45,
            },
            {
                "kind": "blade_extends",
                "from": "view:orders.show",
                "to": "view:layouts.app",
                "path": "resources/views/orders/show.blade.php",
                "line": 1,
            },
            {
                "kind": "blade_component",
                "from": "view:orders.show",
                "to": "component:alert",
                "path": "resources/views/orders/show.blade.php",
                "line": 4,
            },
            {
                "kind": "test_covers_symbol",
                "from": "test:tests/Feature/OrderControllerTest.php",
                "to": "OrderController@show",
                "path": "tests/Feature/OrderControllerTest.php",
            },
            {
                "kind": "emits_log",
                "from": "OrderController@show",
                "to": "log:order-show-warning",
                "level": "warning",
                "logger": "Log",
                "path": "app/Http/Controllers/OrderController.php",
                "line": 47,
            },
            {
                "kind": "query_operation",
                "from": "OrderController@show",
                "to": "query:orders:update",
                "table": "orders",
                "operation": "update",
                "access": "write",
                "path": "app/Http/Controllers/OrderController.php",
                "line": 49,
            },
            {
                "kind": "query_write",
                "from": "OrderController@show",
                "to": "table:orders",
                "query_method": "update",
                "path": "app/Http/Controllers/OrderController.php",
                "line": 49,
            },
            {
                "kind": "model_fillable",
                "from": "App\\Models\\Order",
                "to": "table:orders.status",
                "field": "status",
                "table": "orders",
                "path": "app/Models/Order.php",
                "line": 6,
            },
            {
                "kind": "model_cast",
                "from": "App\\Models\\Order",
                "to": "table:orders.status",
                "field": "status",
                "cast_type": "string",
                "table": "orders",
                "path": "app/Models/Order.php",
                "line": 8,
            },
            {
                "kind": "model_hidden",
                "from": "App\\Models\\Order",
                "to": "table:orders.internal_note",
                "field": "internal_note",
                "property": "hidden",
                "table": "orders",
                "path": "app/Models/Order.php",
                "line": 9,
            },
            {
                "kind": "model_visible",
                "from": "App\\Models\\Order",
                "to": "table:orders.display_status",
                "field": "display_status",
                "property": "visible",
                "table": "orders",
                "path": "app/Models/Order.php",
                "line": 10,
            },
            {
                "kind": "model_appended_attribute",
                "from": "App\\Models\\Order",
                "to": "model_attribute:App\\Models\\Order.display_status",
                "field": "display_status",
                "property": "appends",
                "table": "orders",
                "path": "app/Models/Order.php",
                "line": 11,
            },
            {
                "kind": "api_resource_model",
                "from": "App\\Http\\Resources\\OrderResource",
                "to": "App\\Models\\Order",
                "table": "orders",
                "path": "app/Http/Resources/OrderResource.php",
                "line": 4,
            },
            {
                "kind": "api_resource_table",
                "from": "App\\Http\\Resources\\OrderResource",
                "to": "table:orders",
                "model": "App\\Models\\Order",
                "path": "app/Http/Resources/OrderResource.php",
                "line": 4,
            },
            {
                "kind": "api_resource_field",
                "from": "App\\Http\\Resources\\OrderResource",
                "to": "response_field:id",
                "field": "id",
                "model": "App\\Models\\Order",
                "table": "orders",
                "path": "app/Http/Resources/OrderResource.php",
                "line": 6,
            },
            {
                "kind": "api_resource_field",
                "from": "App\\Http\\Resources\\OrderResource",
                "to": "response_field:status",
                "field": "status",
                "model": "App\\Models\\Order",
                "table": "orders",
                "path": "app/Http/Resources/OrderResource.php",
                "line": 6,
            },
            {
                "kind": "api_resource_ref",
                "from": "OrderController@show",
                "to": "App\\Http\\Resources\\OrderResource",
                "resource_method": "make",
                "model": "App\\Models\\Order",
                "table": "orders",
                "path": "app/Http/Controllers/OrderController.php",
                "line": 50,
            },
            {
                "kind": "model_accessor",
                "from": "App\\Models\\Order",
                "to": "table:orders.display_status",
                "field": "display_status",
                "direction": "get",
                "attribute_style": "classic",
                "attribute_method": "getDisplayStatusAttribute",
                "table": "orders",
                "path": "app/Models/Order.php",
                "line": 13,
            },
            {
                "kind": "model_mutator",
                "from": "App\\Models\\Order",
                "to": "table:orders.normalized_status",
                "field": "normalized_status",
                "direction": "set",
                "attribute_style": "attribute_object",
                "attribute_method": "normalizedStatus",
                "table": "orders",
                "path": "app/Models/Order.php",
                "line": 16,
            },
            {
                "kind": "model_scope",
                "from": "App\\Models\\Order",
                "to": "scope:App\\Models\\Order.recent",
                "scope": "recent",
                "method": "scopeRecent",
                "path": "app/Models/Order.php",
                "line": 15,
            },
            {
                "kind": "scope_method",
                "from": "scope:App\\Models\\Order.recent",
                "to": "Order@scopeRecent",
                "scope": "recent",
                "model": "App\\Models\\Order",
                "path": "app/Models/Order.php",
                "line": 15,
            },
            {
                "kind": "eloquent_scope_call",
                "from": "OrderController@show",
                "to": "scope:App\\Models\\Order.recent",
                "scope": "recent",
                "model": "App\\Models\\Order",
                "table": "orders",
                "path": "app/Http/Controllers/OrderController.php",
                "line": 50,
            },
        ],
        "tests": {
            "schema": "hades.test_map.v1",
            "file_count": 1,
            "files": [
                {
                    "path": "tests/Feature/OrderControllerTest.php",
                    "language": "php",
                    "framework": "phpunit",
                    "test_count": 1,
                    "cases": [{"name": "test_show_order", "line": 12, "ordinal": 1}],
                    "target_candidates": ["OrderController"],
                    "symbol_refs": ["OrderController@show"],
                    "route_refs": ["route:orders.show"],
                    "import_count": 1,
                }
            ],
            "truncated": False,
            "raw_source_included": False,
        },
        "logs": {
            "schema": "hades.log_map.v1",
            "event_count": 1,
            "events": [
                {
                    "id": "log:order-show-warning",
                    "context": "OrderController@show",
                    "logger": "Log",
                    "level": "warning",
                    "path": "app/Http/Controllers/OrderController.php",
                    "line": 47,
                    "message_sha256": "a" * 64,
                    "message_length": 28,
                }
            ],
            "truncated": False,
            "raw_source_included": False,
        },
        "raw_source_included": False,
    }


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
        "hades_backend_project_memory_search",
        "hades_backend_bug_evidence_search",
        "hades_backend_graph_search",
        "hades_backend_graph_traverse",
        "hades_backend_source_slice_fetch",
        "hades_backend_evidence_pack_search",
        "hades_backend_evidence_pack_create",
        "hades_backend_project_awareness_status",
        "hades_backend_diagnosis_report_create",
        "hades_backend_resolved_bug_promote",
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


def test_hades_backend_memory_search_tool_exposes_resolved_bug_status(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)

    class FakeClient:
        def __init__(self):
            self.calls = []

        def memory_search(self, **payload):
            self.calls.append(payload)
            return {
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "version": "search_v1",
                "etag": "search_v1",
                "query": payload["query"],
                "domain": payload["domain"],
                "kind": payload["kind"],
                "include_raw_chunks": payload["include_raw_chunks"],
                "count": 2,
                "candidate_count": 2,
                "truncated": False,
                "raw_chunks_omitted": 0,
                "items": [
                    {
                        "id": "mem_bug_1",
                        "domain": "project_memory",
                        "kind": "resolved_bug",
                        "schema": "hades.resolved_bug.v1",
                        "source": "hades_diagnosis_report",
                        "summary": "Resolved bug: active() on null in OrderController.",
                        "score": 42,
                        "raw_chunk": False,
                        "stale": True,
                        "stale_reason": "workspace_head_changed",
                    },
                    {
                        "id": "mem_note_1",
                        "domain": "project_memory",
                        "kind": "agent_note",
                        "summary": "Generic note that should not survive kind filtering.",
                        "score": 10,
                        "raw_chunk": False,
                    }
                ],
            }

        def close(self):
            pass

    fake = FakeClient()
    import plugins.memory.hades_backend as hades_memory

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", lambda *, timeout=None: fake)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_project_memory_search",
            {"query": "active null", "domain": "project_memory", "kind": "resolved_bug", "limit": 5},
        )
    )

    assert fake.calls[0]["kind"] == "resolved_bug"
    assert result["kind"] == "resolved_bug"
    assert result["count"] == 1
    assert len(result["items"]) == 1
    assert result["items"][0]["kind"] == "resolved_bug"
    assert result["items"][0]["stale"] is True
    assert result["items"][0]["stale_reason"] == "workspace_head_changed"


def test_hades_backend_memory_search_tool_passes_structured_filters(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)

    class FakeClient:
        def __init__(self):
            self.calls = []

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
                "count": 2,
                "candidate_count": 2,
                "truncated": False,
                "raw_chunks_omitted": 0,
                "items": [
                    {
                        "id": "mem_bug_1",
                        "domain": "project_memory",
                        "kind": "resolved_bug",
                        "schema": "hades.resolved_bug.v1",
                        "source": "hades_diagnosis_report",
                        "summary": "Resolved bug: active() on null in OrderController.",
                        "payload": {
                            "affected_symbols": ["App\\Http\\Controllers\\OrderController@show"],
                            "path": "app/Http/Controllers/OrderController.php",
                        },
                        "match_fields": ["payload.affected_symbols", "payload.path"],
                        "score": 64,
                        "raw_chunk": False,
                    },
                    {
                        "id": "mem_note_1",
                        "domain": "project_memory",
                        "kind": "agent_note",
                        "schema": "hades.agent_note.v1",
                        "source": "hades_agent",
                        "summary": "OrderController note without diagnosis status.",
                        "score": 10,
                        "raw_chunk": False,
                    },
                ],
            }

        def close(self):
            pass

    fake = FakeClient()
    import plugins.memory.hades_backend as hades_memory

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", lambda *, timeout=None: fake)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_project_memory_search",
            {
                "query": "order active null",
                "domain": "project_memory",
                "kind": "resolved_bug",
                "schema": "hades.resolved_bug.v1",
                "source": "hades_diagnosis_report",
                "symbol": "OrderController@show",
                "path": "OrderController.php",
                "limit": 5,
            },
        )
    )

    assert fake.calls[0]["kind"] == "resolved_bug"
    assert fake.calls[0]["schema"] == "hades.resolved_bug.v1"
    assert fake.calls[0]["source"] == "hades_diagnosis_report"
    assert fake.calls[0]["symbol"] == "OrderController@show"
    assert fake.calls[0]["path"] == "OrderController.php"
    assert result["filters"] == {
        "kind": "resolved_bug",
        "schema": "hades.resolved_bug.v1",
        "source": "hades_diagnosis_report",
        "symbol": "OrderController@show",
        "path": "OrderController.php",
    }
    assert result["count"] == 1
    assert result["items"][0]["id"] == "mem_bug_1"
    assert result["items"][0]["match_fields"] == ["payload.affected_symbols", "payload.path"]


def test_hades_backend_memory_search_tool_filters_cache_by_structured_fields(monkeypatch, tmp_path):
    provider = _create_linked_provider(
        monkeypatch,
        tmp_path,
        items=[
            {
                "id": "mem_bug_1",
                "domain": "project_memory",
                "kind": "resolved_bug",
                "schema": "hades.resolved_bug.v1",
                "source": "hades_diagnosis_report",
                "summary": "Resolved bug: active() on null in OrderController.",
                "payload": {
                    "affected_symbols": ["App\\Http\\Controllers\\OrderController@show"],
                    "path": "app/Http/Controllers/OrderController.php",
                },
            },
            {
                "id": "mem_bug_2",
                "domain": "project_memory",
                "kind": "resolved_bug",
                "schema": "hades.resolved_bug.v1",
                "source": "hades_diagnosis_report",
                "summary": "Resolved bug: checkout timeout in PaymentController.",
                "payload": {
                    "affected_symbols": ["App\\Http\\Controllers\\PaymentController@store"],
                    "path": "app/Http/Controllers/PaymentController.php",
                },
            },
        ],
    )

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_project_memory_search",
            {
                "query": "active null",
                "domain": "project_memory",
                "kind": "resolved_bug",
                "schema": "hades.resolved_bug.v1",
                "symbol": "OrderController@show",
                "path": "OrderController.php",
                "limit": 5,
            },
        )
    )

    assert result["searched_cache_only"] is True
    assert result["filters"]["symbol"] == "OrderController@show"
    assert result["filters"]["path"] == "OrderController.php"
    assert result["count"] == 1
    assert result["items"][0]["id"] == "mem_bug_1"


def test_hades_backend_graph_search_tool_queries_artifacts_live(monkeypatch, tmp_path):
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
                "version": "graph_search_v1",
                "etag": "graph_search_v1",
                "query": payload["query"],
                "domain": payload["domain"],
                "include_raw_chunks": payload["include_raw_chunks"],
                "count": 1,
                "candidate_count": 1,
                "truncated": False,
                "raw_chunks_omitted": 0,
                "freshness": {"index_status": "live_query"},
                "items": [
                    {
                        "id": "artifact_1",
                        "domain": "artifacts",
                        "schema": "hades.php_graph.v1",
                        "source": "hades.php_graph.v1",
                        "summary": "GET /orders/{order} -> OrderController@show",
                        "score": 21,
                        "raw_chunk": False,
                    }
                ],
            }

        def close(self):
            self.closed += 1

    fake = FakeClient()
    import plugins.memory.hades_backend as hades_memory

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", lambda *, timeout=None: fake)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_graph_search",
            {"query": "OrderController show", "limit": 4},
        )
    )

    assert result["status"] == "ok"
    assert result["tool_domain"] == "graph"
    assert result["domain"] == "artifacts"
    assert result["items"][0]["schema"] == "hades.php_graph.v1"
    assert fake.calls == [
        {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "query": "OrderController show",
            "domain": "artifacts",
            "limit": 4,
            "include_raw_chunks": False,
        }
    ]
    assert fake.closed == 1


def test_hades_backend_graph_search_falls_back_to_local_graph_cache(monkeypatch, tmp_path):
    provider = _create_linked_provider(
        monkeypatch,
        tmp_path,
        items=[
            {
                "id": "artifact_1",
                "domain": "artifacts",
                "schema": "hades.php_graph.v1",
                "source": "hades.php_graph.v1",
                "summary": "Laravel graph artifact for order route.",
                "payload": _php_graph_artifact(),
            }
        ],
    )

    import plugins.memory.hades_backend as hades_memory

    def unavailable_client(*, timeout=None):
        raise RuntimeError("backend offline")

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", unavailable_client)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_graph_search",
            {"query": "OrderController show", "limit": 5},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["tool_domain"] == "graph"
    assert result["domain"] == "artifacts"
    assert result["searched_cache_only"] is True
    assert result["schema"] == "hades.php_graph.v1"
    assert result["ranking"] == "local_bm25"
    assert result["freshness"]["status"] == "cached"
    assert result["backend_live_error"] == "backend offline"
    assert result["count"] >= 1
    assert result["candidate_count"] >= result["count"]
    assert any("bm25" in item["match_fields"] for item in result["items"])
    assert any(ref["type"] == "node" and ref["id"] == "OrderController@show" for ref in graph_refs)
    assert any(ref["type"] == "edge" and ref["kind"] == "route_handler" for ref in graph_refs)


def test_hades_backend_graph_search_finds_local_resource_routes(monkeypatch, tmp_path):
    provider = _create_linked_provider(
        monkeypatch,
        tmp_path,
        items=[
            {
                "id": "artifact_1",
                "domain": "artifacts",
                "schema": "hades.php_graph.v1",
                "source": "hades.php_graph.v1",
                "summary": "Laravel graph artifact for order route.",
                "payload": _php_graph_artifact(),
            }
        ],
    )

    import plugins.memory.hades_backend as hades_memory

    def unavailable_client(*, timeout=None):
        raise RuntimeError("backend offline")

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", unavailable_client)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_graph_search",
            {"query": "invoices resource index", "limit": 5},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "node"
        and ref["id"] == "route:invoices.index"
        and ref["attributes"]["resource"] == "invoices"
        and ref["attributes"]["resource_action"] == "index"
        for ref in graph_refs
    )
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "route_handler"
        and ref["from"] == "route:invoices.index"
        for ref in graph_refs
    )


def test_hades_backend_graph_search_finds_local_test_map_nodes(monkeypatch, tmp_path):
    provider = _create_linked_provider(
        monkeypatch,
        tmp_path,
        items=[
            {
                "id": "artifact_1",
                "domain": "artifacts",
                "schema": "hades.php_graph.v1",
                "source": "hades.php_graph.v1",
                "summary": "Laravel graph artifact for order route.",
                "payload": _php_graph_artifact(),
            }
        ],
    )

    import plugins.memory.hades_backend as hades_memory

    def unavailable_client(*, timeout=None):
        raise RuntimeError("backend offline")

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", unavailable_client)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_graph_search",
            {"query": "OrderControllerTest", "limit": 5},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "node"
        and ref["id"] == "test:tests/Feature/OrderControllerTest.php"
        and ref["kind"] == "test_file"
        for ref in graph_refs
    )
    assert any(ref["type"] == "edge" and ref["kind"] == "test_covers_symbol" for ref in graph_refs)


def test_hades_backend_graph_search_finds_local_log_map_nodes(monkeypatch, tmp_path):
    provider = _create_linked_provider(
        monkeypatch,
        tmp_path,
        items=[
            {
                "id": "artifact_1",
                "domain": "artifacts",
                "schema": "hades.php_graph.v1",
                "source": "hades.php_graph.v1",
                "summary": "Laravel graph artifact for order route.",
                "payload": _php_graph_artifact(),
            }
        ],
    )

    import plugins.memory.hades_backend as hades_memory

    def unavailable_client(*, timeout=None):
        raise RuntimeError("backend offline")

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", unavailable_client)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_graph_search",
            {"query": "warning Log OrderController", "limit": 5},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "node"
        and ref["id"] == "log:order-show-warning"
        and ref["kind"] == "log_event"
        for ref in graph_refs
    )
    assert any(ref["type"] == "edge" and ref["kind"] == "emits_log" for ref in graph_refs)


def test_hades_backend_graph_search_finds_local_query_write_edges(monkeypatch, tmp_path):
    provider = _create_linked_provider(
        monkeypatch,
        tmp_path,
        items=[
            {
                "id": "artifact_1",
                "domain": "artifacts",
                "schema": "hades.php_graph.v1",
                "source": "hades.php_graph.v1",
                "summary": "Laravel graph artifact for order route.",
                "payload": _php_graph_artifact(),
            }
        ],
    )

    import plugins.memory.hades_backend as hades_memory

    def unavailable_client(*, timeout=None):
        raise RuntimeError("backend offline")

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", unavailable_client)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_graph_search",
            {"query": "orders update write", "limit": 5},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(ref["type"] == "edge" and ref["kind"] == "query_write" for ref in graph_refs)
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "query_operation"
        and ref["to"] == "query:orders:update"
        for ref in graph_refs
    )
    assert any(
        "operation=update" in item["summary"]
        and "access=write" in item["summary"]
        and "table=orders" in item["summary"]
        for item in result["items"]
    )


def test_hades_backend_graph_search_finds_local_model_metadata_edges(monkeypatch, tmp_path):
    provider = _create_linked_provider(
        monkeypatch,
        tmp_path,
        items=[
            {
                "id": "artifact_1",
                "domain": "artifacts",
                "schema": "hades.php_graph.v1",
                "source": "hades.php_graph.v1",
                "summary": "Laravel graph artifact for order route.",
                "payload": _php_graph_artifact(),
            }
        ],
    )

    import plugins.memory.hades_backend as hades_memory

    def unavailable_client(*, timeout=None):
        raise RuntimeError("backend offline")

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", unavailable_client)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_graph_search",
            {"query": "orders status model metadata", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "model_fillable"
        and ref["to"] == "table:orders.status"
        and ref["provenance"]["field"] == "status"
        for ref in graph_refs
    )
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "model_cast"
        and ref["to"] == "table:orders.status"
        and ref["provenance"]["cast_type"] == "string"
        for ref in graph_refs
    )
    assert any("field=status" in item["summary"] for item in result["items"])
    assert any("cast_type=string" in item["summary"] for item in result["items"])


def test_hades_backend_graph_search_finds_local_model_scope_edges(monkeypatch, tmp_path):
    provider = _create_linked_provider(
        monkeypatch,
        tmp_path,
        items=[
            {
                "id": "artifact_1",
                "domain": "artifacts",
                "schema": "hades.php_graph.v1",
                "source": "hades.php_graph.v1",
                "summary": "Laravel graph artifact for order route.",
                "payload": _php_graph_artifact(),
            }
        ],
    )

    import plugins.memory.hades_backend as hades_memory

    def unavailable_client(*, timeout=None):
        raise RuntimeError("backend offline")

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", unavailable_client)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_graph_search",
            {"query": "Order recent scope", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "model_scope"
        and ref["to"] == "scope:App\\Models\\Order.recent"
        for ref in graph_refs
    )
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "eloquent_scope_call"
        and ref["from"] == "OrderController@show"
        and ref["provenance"]["scope"] == "recent"
        for ref in graph_refs
    )
    assert any("scope=recent" in item["summary"] for item in result["items"])


def test_hades_backend_graph_search_finds_local_model_attribute_edges(monkeypatch, tmp_path):
    provider = _create_linked_provider(
        monkeypatch,
        tmp_path,
        items=[
            {
                "id": "artifact_1",
                "domain": "artifacts",
                "schema": "hades.php_graph.v1",
                "source": "hades.php_graph.v1",
                "summary": "Laravel graph artifact for order route.",
                "payload": _php_graph_artifact(),
            }
        ],
    )

    import plugins.memory.hades_backend as hades_memory

    def unavailable_client(*, timeout=None):
        raise RuntimeError("backend offline")

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", unavailable_client)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_graph_search",
            {"query": "normalized status mutator attribute", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "model_mutator"
        and ref["to"] == "table:orders.normalized_status"
        and ref["provenance"]["direction"] == "set"
        for ref in graph_refs
    )
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "model_accessor"
        and ref["to"] == "table:orders.display_status"
        for ref in graph_refs
    )
    assert any("field=normalized_status" in item["summary"] for item in result["items"])
    assert any("attribute_style=attribute_object" in item["summary"] for item in result["items"])


def test_hades_backend_graph_search_finds_local_model_serialization_edges(monkeypatch, tmp_path):
    provider = _create_linked_provider(
        monkeypatch,
        tmp_path,
        items=[
            {
                "id": "artifact_1",
                "domain": "artifacts",
                "schema": "hades.php_graph.v1",
                "source": "hades.php_graph.v1",
                "summary": "Laravel graph artifact for order route.",
                "payload": _php_graph_artifact(),
            }
        ],
    )

    import plugins.memory.hades_backend as hades_memory

    def unavailable_client(*, timeout=None):
        raise RuntimeError("backend offline")

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", unavailable_client)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_graph_search",
            {"query": "display status appends hidden serialization", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "model_appended_attribute"
        and ref["to"] == "model_attribute:App\\Models\\Order.display_status"
        and ref["provenance"]["property"] == "appends"
        for ref in graph_refs
    )
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "model_hidden"
        and ref["to"] == "table:orders.internal_note"
        for ref in graph_refs
    )
    assert any("property=appends" in item["summary"] for item in result["items"])
    assert any("field=display_status" in item["summary"] for item in result["items"])


def test_hades_backend_graph_search_finds_local_api_resource_edges(monkeypatch, tmp_path):
    provider = _create_linked_provider(
        monkeypatch,
        tmp_path,
        items=[
            {
                "id": "artifact_1",
                "domain": "artifacts",
                "schema": "hades.php_graph.v1",
                "source": "hades.php_graph.v1",
                "summary": "Laravel graph artifact for order route.",
                "payload": _php_graph_artifact(),
            }
        ],
    )

    import plugins.memory.hades_backend as hades_memory

    def unavailable_client(*, timeout=None):
        raise RuntimeError("backend offline")

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", unavailable_client)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_graph_search",
            {"query": "OrderResource make orders api resource", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "api_resource_ref"
        and ref["to"] == "App\\Http\\Resources\\OrderResource"
        and ref["provenance"]["resource_method"] == "make"
        for ref in graph_refs
    )
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "api_resource_table"
        and ref["to"] == "table:orders"
        for ref in graph_refs
    )
    assert any("resource_method=make" in item["summary"] for item in result["items"])
    assert any("model=App\\Models\\Order" in item["summary"] for item in result["items"])


def test_hades_backend_graph_search_finds_local_api_resource_field_edges(monkeypatch, tmp_path):
    provider = _create_linked_provider(
        monkeypatch,
        tmp_path,
        items=[
            {
                "id": "artifact_1",
                "domain": "artifacts",
                "schema": "hades.php_graph.v1",
                "source": "hades.php_graph.v1",
                "summary": "Laravel graph artifact for order route.",
                "payload": _php_graph_artifact(),
            }
        ],
    )

    import plugins.memory.hades_backend as hades_memory

    def unavailable_client(*, timeout=None):
        raise RuntimeError("backend offline")

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", unavailable_client)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_graph_search",
            {"query": "OrderResource status response field", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "api_resource_field"
        and ref["to"] == "response_field:status"
        and ref["provenance"]["field"] == "status"
        for ref in graph_refs
    )
    assert any("field=status" in item["summary"] for item in result["items"])
    assert any("model=App\\Models\\Order" in item["summary"] for item in result["items"])


def test_hades_backend_graph_search_finds_local_route_model_bindings(monkeypatch, tmp_path):
    provider = _create_linked_provider(
        monkeypatch,
        tmp_path,
        items=[
            {
                "id": "artifact_1",
                "domain": "artifacts",
                "schema": "hades.php_graph.v1",
                "source": "hades.php_graph.v1",
                "summary": "Laravel graph artifact for order route.",
                "payload": _php_graph_artifact(),
            }
        ],
    )

    import plugins.memory.hades_backend as hades_memory

    def unavailable_client(*, timeout=None):
        raise RuntimeError("backend offline")

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", unavailable_client)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_graph_search",
            {"query": "route model binding orders order", "limit": 5},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(ref["type"] == "edge" and ref["kind"] == "route_model_binding" for ref in graph_refs)
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "route_model_table"
        and ref["to"] == "table:orders"
        for ref in graph_refs
    )
    assert any(
        "param=order" in item["summary"]
        and "handler=OrderController@show" in item["summary"]
        and "uri=/orders/{order}" in item["summary"]
        for item in result["items"]
    )


def test_hades_backend_graph_search_finds_local_route_validation_edges(monkeypatch, tmp_path):
    provider = _create_linked_provider(
        monkeypatch,
        tmp_path,
        items=[
            {
                "id": "artifact_1",
                "domain": "artifacts",
                "schema": "hades.php_graph.v1",
                "source": "hades.php_graph.v1",
                "summary": "Laravel graph artifact for order route.",
                "payload": _php_graph_artifact(),
            }
        ],
    )

    import plugins.memory.hades_backend as hades_memory

    def unavailable_client(*, timeout=None):
        raise RuntimeError("backend offline")

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", unavailable_client)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_graph_search",
            {"query": "orders customer_id validation request", "limit": 5},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(ref["type"] == "edge" and ref["kind"] == "route_uses_form_request" for ref in graph_refs)
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "route_request_validation"
        and ref["to"] == "validation:customer_id"
        for ref in graph_refs
    )
    assert any(
        "request_class=App\\Http\\Requests\\StoreOrderRequest" in item["summary"]
        and "validation_path=app/Http/Requests/StoreOrderRequest.php" in item["summary"]
        and "validation_rules=['required', 'integer', 'exists']" in item["summary"]
        for item in result["items"]
    )


def test_hades_backend_graph_search_finds_local_authorization_edges(monkeypatch, tmp_path):
    provider = _create_linked_provider(
        monkeypatch,
        tmp_path,
        items=[
            {
                "id": "artifact_1",
                "domain": "artifacts",
                "schema": "hades.php_graph.v1",
                "source": "hades.php_graph.v1",
                "summary": "Laravel graph artifact for order route.",
                "payload": _php_graph_artifact(),
            }
        ],
    )

    import plugins.memory.hades_backend as hades_memory

    def unavailable_client(*, timeout=None):
        raise RuntimeError("backend offline")

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", unavailable_client)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_graph_search",
            {"query": "orders authorization view policy", "limit": 5},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(ref["type"] == "edge" and ref["kind"] == "route_authorization" for ref in graph_refs)
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "route_authorization_table"
        and ref["to"] == "table:orders"
        for ref in graph_refs
    )
    assert any(
        "ability=view" in item["summary"]
        and "target_model=App\\Models\\Order" in item["summary"]
        and "source=this_authorize" in item["summary"]
        for item in result["items"]
    )


def test_hades_backend_graph_traverse_tool_reads_live_backend(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)
    timeouts = []

    class FakeClient:
        def __init__(self):
            self.calls = []
            self.closed = 0

        def graph_traverse(self, **payload):
            self.calls.append(payload)
            return {
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "version": "graph_traversal_1",
                "etag": "graph_traversal_1",
                "artifact_id": "artifact_1",
                "schema": "hades.php_graph.v1",
                "head_commit": "abc123",
                "start": payload["start"],
                "direction": payload["direction"],
                "max_depth": payload["max_depth"],
                "limit": payload["limit"],
                "count": 2,
                "edge_count": 1,
                "truncated": False,
                "match_fields": ["id", "attributes.name"],
                "freshness": {"status": "current", "workspace_head_commit": "abc123"},
                "provenance": {"artifact_id": "artifact_1", "schema": "hades.php_graph.v1"},
                "nodes": [
                    {"id": "route:orders.show", "kind": "route", "label": "orders.show"},
                    {"id": "OrderController@show", "kind": "method", "label": "OrderController@show"},
                ],
                "edges": [
                    {
                        "id": "edge_1",
                        "kind": "route_handler",
                        "from": "route:orders.show",
                        "to": "OrderController@show",
                    }
                ],
                "server_time": "2026-07-07T13:00:00Z",
            }

        def close(self):
            self.closed += 1

    fake = FakeClient()
    import plugins.memory.hades_backend as hades_memory

    def client_from_config(*, timeout=None):
        timeouts.append(timeout)
        return fake

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", client_from_config)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_graph_traverse",
            {"start": "orders.show", "direction": "out", "max_depth": 2, "limit": 10},
        )
    )

    assert result["status"] == "ok"
    assert result["artifact_id"] == "artifact_1"
    assert result["freshness"]["status"] == "current"
    assert result["nodes"][0]["id"] == "route:orders.show"
    assert result["edges"][0]["kind"] == "route_handler"
    assert fake.calls == [
        {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "start": "orders.show",
            "direction": "out",
            "max_depth": 2,
            "limit": 10,
        }
    ]
    assert fake.closed == 1
    assert timeouts == [0.75]


def test_hades_backend_graph_traverse_falls_back_to_local_graph_cache(monkeypatch, tmp_path):
    provider = _create_linked_provider(
        monkeypatch,
        tmp_path,
        items=[
            {
                "id": "artifact_1",
                "domain": "artifacts",
                "schema": "hades.php_graph.v1",
                "source": "hades.php_graph.v1",
                "summary": "Laravel graph artifact for order route.",
                "payload": _php_graph_artifact(),
            }
        ],
    )

    import plugins.memory.hades_backend as hades_memory

    def unavailable_client(*, timeout=None):
        raise RuntimeError("backend offline")

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", unavailable_client)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_graph_traverse",
            {"start": "route:orders.show", "direction": "out", "max_depth": 3, "limit": 40},
        )
    )

    node_ids = {node["id"] for node in result["nodes"]}
    edge_kinds = {edge["kind"] for edge in result["edges"]}

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert result["artifact_id"] == "artifact_1"
    assert result["schema"] == "hades.php_graph.v1"
    assert result["freshness"]["status"] == "cached"
    assert result["freshness"]["index_status"] == "local_graph_cache"
    assert result["backend_live_error"] == "backend offline"
    assert "id" in result["match_fields"]
    assert {
        "route:orders.show",
        "OrderController@show",
        "view:orders.show",
        "view:layouts.app",
        "component:alert",
    } <= node_ids
    assert {"route_handler", "view_ref", "blade_extends", "blade_component"} <= edge_kinds
    assert result["provenance"]["artifacts"][0]["origin"] == "memory_cache"


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

    assert timeouts == [0.75]


def test_hades_backend_bug_evidence_search_tool_prefers_live_backend(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)

    class FakeClient:
        def __init__(self):
            self.calls = []
            self.closed = 0

        def bug_evidence_search(self, **payload):
            self.calls.append(payload)
            return {
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "version": "bug_evidence_search_v1",
                "etag": "bug_evidence_search_v1",
                "query": payload["query"],
                "kind": payload["kind"],
                "bug_report_id": payload["bug_report_id"],
                "count": 1,
                "candidate_count": 1,
                "truncated": False,
                "freshness": {
                    "workspace_head_commit": "abc123",
                    "index_status": "live_query",
                },
                "server_time": "2026-07-07T12:00:00Z",
                "items": [
                    {
                        "id": "evidence_1",
                        "bug_report_id": "bug_1",
                        "kind": "stack_trace",
                        "summary": "Call to member function active() on null in SecurityActivityCategoryController.",
                        "source": "laravel.log",
                        "payload": {
                            "frames": [
                                {
                                    "file": "app/Http/Controllers/Taxonomy/SecurityActivityCategoryController.php",
                                    "line": 42,
                                }
                            ]
                        },
                        "sha256": "a" * 64,
                        "redactions": 1,
                        "retention_class": "stack_trace",
                        "occurred_at": "2026-07-07T11:58:00Z",
                        "score": 42,
                        "version": "bug_evidence_1",
                    }
                ],
            }

        def close(self):
            self.closed += 1

    fake = FakeClient()
    import plugins.memory.hades_backend as hades_memory

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", lambda *, timeout=None: fake)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_bug_evidence_search",
            {
                "query": "SecurityActivityCategoryController active null",
                "kind": "stack_trace",
                "bug_report_id": "bug_1",
                "limit": 5,
            },
        )
    )

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is False
    assert result["backend_version"] == "bug_evidence_search_v1"
    assert result["kind"] == "stack_trace"
    assert result["bug_report_id"] == "bug_1"
    assert result["freshness"]["index_status"] == "live_query"
    assert result["items"][0]["id"] == "evidence_1"
    assert result["items"][0]["payload"]["frames"][0]["line"] == 42
    assert result["items"][0]["graph_refs"] == [
        {
            "type": "source_frame",
            "path": "app/Http/Controllers/Taxonomy/SecurityActivityCategoryController.php",
            "line": 42,
            "graph_query": "app/Http/Controllers/Taxonomy/SecurityActivityCategoryController.php",
        }
    ]
    assert fake.calls == [
        {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "query": "SecurityActivityCategoryController active null",
            "kind": "stack_trace",
            "bug_report_id": "bug_1",
            "limit": 5,
        }
    ]
    assert fake.closed == 1


def test_hades_backend_bug_evidence_search_tool_uses_short_timeout(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)
    timeouts = []

    class FakeClient:
        def bug_evidence_search(self, **payload):
            return {
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "version": "bug_evidence_search_v1",
                "query": payload["query"],
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
        "hades_backend_bug_evidence_search",
        {"query": "stack trace"},
    )

    assert timeouts == [0.75]


def test_hades_backend_source_slice_fetch_tool_prefers_live_backend(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)
    timeouts = []

    class FakeClient:
        def __init__(self):
            self.calls = []
            self.closed = 0

        def source_slices(self, **payload):
            self.calls.append(payload)
            return {
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "version": "source_slice_search_v1",
                "etag": "source_slice_search_v1",
                "query": payload["query"],
                "path": payload["path"],
                "symbol": payload["symbol"],
                "line": payload["line"],
                "count": 1,
                "candidate_count": 1,
                "truncated": False,
                "freshness": {
                    "workspace_head_commit": "abc123",
                    "index_status": "live_query",
                },
                "server_time": "2026-07-07T12:00:00Z",
                "items": [
                    {
                        "id": "slice_1",
                        "path": "app/Http/Controllers/OrderController.php",
                        "start_line": 41,
                        "end_line": 43,
                        "language": "php",
                        "symbol": "OrderController@show",
                        "head_commit": "abc123",
                        "sha256": "b" * 64,
                        "content_redacted": "41: public function show() {\n42:     return ***;\n43: }",
                        "redactions": 1,
                        "truncated": False,
                        "retention_class": "source_slice",
                        "policy": "manual_review",
                        "updated_at": "2026-07-07T11:59:00Z",
                        "score": 25,
                        "version": "source_slice_1",
                    }
                ],
            }

        def close(self):
            self.closed += 1

    fake = FakeClient()
    import plugins.memory.hades_backend as hades_memory

    def client_from_config(*, timeout=None):
        timeouts.append(timeout)
        return fake

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", client_from_config)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_source_slice_fetch",
            {
                "query": "OrderController show",
                "path": "app/Http/Controllers/OrderController.php",
                "symbol": "OrderController@show",
                "line": 42,
                "limit": 3,
            },
        )
    )

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is False
    assert result["backend_version"] == "source_slice_search_v1"
    assert result["freshness"]["index_status"] == "live_query"
    assert result["items"][0]["id"] == "slice_1"
    assert result["items"][0]["start_line"] == 41
    assert result["items"][0]["content_redacted"].endswith("}")
    assert result["items"][0]["redactions"] == 1
    assert fake.calls == [
        {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "id": None,
            "query": "OrderController show",
            "path": "app/Http/Controllers/OrderController.php",
            "symbol": "OrderController@show",
            "line": 42,
            "limit": 3,
        }
    ]
    assert fake.closed == 1
    assert timeouts == [1.25]


def test_hades_backend_source_slice_fetch_tool_requires_scope(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)

    result = json.loads(provider.handle_tool_call("hades_backend_source_slice_fetch", {}))

    assert result["error"].startswith("Provide at least one")


def test_hades_backend_evidence_pack_search_tool_prefers_live_backend(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)
    timeouts = []

    class FakeClient:
        def __init__(self):
            self.calls = []
            self.closed = 0

        def evidence_packs(self, **payload):
            self.calls.append(payload)
            return {
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "version": "evidence_pack_search_v1",
                "etag": "evidence_pack_search_v1",
                "query": payload["query"],
                "bug_report_id": payload["bug_report_id"],
                "count": 1,
                "candidate_count": 1,
                "truncated": False,
                "freshness": {"status": "current", "workspace_head_commit": "abc123"},
                "server_time": "2026-07-07T12:00:00Z",
                "items": [
                    {
                        "id": "pack_1",
                        "bug_report_id": "bug_1",
                        "title": "Order route evidence pack",
                        "summary": "Stack trace, graph edge, and source slice point to OrderController.",
                        "evidence_refs": [{"type": "bug_evidence", "id": "evidence_1"}],
                        "graph_refs": [{"type": "route_handler", "to": "OrderController@show"}],
                        "source_slice_ids": ["slice_1"],
                        "payload": {"next_verification": "Run focused test"},
                        "sha256": "c" * 64,
                        "redactions": 1,
                        "retention_class": "diagnosis_evidence",
                        "head_commit": "abc123",
                        "updated_at": "2026-07-07T11:59:00Z",
                        "score": 33,
                        "version": "evidence_pack_1",
                    }
                ],
            }

        def close(self):
            self.closed += 1

    fake = FakeClient()
    import plugins.memory.hades_backend as hades_memory

    def client_from_config(*, timeout=None):
        timeouts.append(timeout)
        return fake

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", client_from_config)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_evidence_pack_search",
            {"query": "OrderController", "bug_report_id": "bug_1", "limit": 5},
        )
    )

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is False
    assert result["backend_version"] == "evidence_pack_search_v1"
    assert result["freshness"]["workspace_head_commit"] == "abc123"
    assert result["items"][0]["id"] == "pack_1"
    assert result["items"][0]["source_slice_ids"] == ["slice_1"]
    assert result["items"][0]["payload"]["next_verification"] == "Run focused test"
    assert fake.calls == [
        {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "id": None,
            "query": "OrderController",
            "bug_report_id": "bug_1",
            "limit": 5,
        }
    ]
    assert fake.closed == 1
    assert timeouts == [0.75]


def test_hades_backend_evidence_pack_search_tool_requires_scope(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)

    result = json.loads(provider.handle_tool_call("hades_backend_evidence_pack_search", {}))

    assert result["error"].startswith("Provide at least one")


def test_hades_backend_evidence_pack_create_tool_persists_live_backend(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)
    timeouts = []

    class FakeClient:
        def __init__(self):
            self.calls = []
            self.closed = 0

        def create_evidence_pack(self, **payload):
            self.calls.append(payload)
            return {
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "server_time": "2026-07-07T12:00:00Z",
                "evidence_pack": {
                    "id": "pack_1",
                    "bug_report_id": payload["bug_report_id"],
                    "title": payload["title"],
                    "summary": payload["summary"],
                    "evidence_refs": payload["evidence_refs"],
                    "graph_refs": payload["graph_refs"],
                    "source_slice_ids": payload["source_slice_ids"],
                    "payload": payload["payload"],
                    "head_commit": payload["head_commit"],
                    "redactions": payload["redactions"],
                    "retention_class": "diagnosis_evidence",
                    "sha256": "d" * 64,
                    "updated_at": "2026-07-07T11:59:00Z",
                    "version": "evidence_pack_1",
                },
            }

        def close(self):
            self.closed += 1

    fake = FakeClient()
    import plugins.memory.hades_backend as hades_memory

    def client_from_config(*, timeout=None):
        timeouts.append(timeout)
        return fake

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", client_from_config)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_evidence_pack_create",
            {
                "bug_report_id": "bug_1",
                "title": "Order route evidence pack",
                "summary": "Stack trace, graph edge, and source slice point to OrderController.",
                "evidence_refs": [{"type": "bug_evidence", "id": "evidence_1"}],
                "graph_refs": [{"type": "route_handler", "to": "OrderController@show"}],
                "source_slice_ids": ["slice_1"],
                "payload": {"next_verification": "Run focused test"},
                "head_commit": "abc123",
                "redactions": 2,
            },
        )
    )

    assert result["status"] == "ok"
    assert result["evidence_pack"]["id"] == "pack_1"
    assert result["evidence_pack"]["source_slice_ids"] == ["slice_1"]
    assert result["evidence_pack"]["payload"]["next_verification"] == "Run focused test"
    assert fake.calls == [
        {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "bug_report_id": "bug_1",
            "title": "Order route evidence pack",
            "summary": "Stack trace, graph edge, and source slice point to OrderController.",
            "evidence_refs": [{"type": "bug_evidence", "id": "evidence_1"}],
            "graph_refs": [{"type": "route_handler", "to": "OrderController@show"}],
            "source_slice_ids": ["slice_1"],
            "payload": {"next_verification": "Run focused test"},
            "head_commit": "abc123",
            "redactions": 2,
        }
    ]
    assert fake.closed == 1
    assert timeouts == [2.0]


def test_hades_backend_evidence_pack_create_tool_requires_title(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_evidence_pack_create",
            {"summary": "Missing title."},
        )
    )

    assert result["error"] == "Missing required parameter: title"


def test_hades_backend_diagnosis_report_create_tool_persists_live_backend(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)
    timeouts = []

    class FakeClient:
        def __init__(self):
            self.calls = []
            self.awareness_calls = []
            self.closed = 0

        def project_awareness_status(self, **payload):
            self.awareness_calls.append(payload)
            return {
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "overall_status": "current",
                "diagnosable_without_source": True,
                "freshness": {
                    "status": "current",
                    "workspace_head_commit": "abc123",
                    "artifact_head_commit": "abc123",
                    "index_status": "live_query",
                },
                "coverage": {"code_graph": {"status": "current", "count": 1}},
                "actions": [],
            }

        def create_diagnosis_report(self, **payload):
            self.calls.append(payload)
            return {
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "server_time": "2026-07-07T12:00:00Z",
                "diagnosis_report": {
                    "id": "diag_1",
                    "bug_report_id": payload["bug_report_id"],
                    "status": payload["status"],
                    "confidence": payload["confidence"],
                    "root_cause": payload["root_cause"],
                    "mechanism": payload["mechanism"],
                    "evidence_refs": payload["evidence_refs"],
                    "freshness": payload["freshness"],
                    "payload": payload["payload"],
                    "redactions": payload["redactions"],
                    "created_at": "2026-07-07T11:59:00Z",
                    "updated_at": "2026-07-07T11:59:00Z",
                    "version": "diagnosis_report_1",
                },
            }

        def close(self):
            self.closed += 1

    fake = FakeClient()
    import plugins.memory.hades_backend as hades_memory

    def client_from_config(*, timeout=None):
        timeouts.append(timeout)
        return fake

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", client_from_config)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_diagnosis_report_create",
            {
                "bug_report_id": "bug_1",
                "status": "final",
                "confidence": "high",
                "root_cause": "OrderController dereferences a missing customer relation.",
                "mechanism": "The show action assumes customer is loaded and calls active().",
                "evidence_refs": [
                    {"type": "bug_evidence", "id": "evidence_1"},
                    {"type": "source_slice", "id": "slice_1"},
                ],
                "freshness": {"status": "current", "workspace_head_commit": "abc123"},
                "awareness": {"diagnosable_without_source": True},
                "payload": {"next_verification": "Run OrderControllerTest::test_show_missing_customer"},
                "redactions": 2,
            },
        )
    )

    assert result["status"] == "ok"
    assert result["diagnosis_report"]["id"] == "diag_1"
    assert result["diagnosis_report"]["confidence"] == "high"
    assert result["diagnosis_report"]["root_cause"].startswith("OrderController")
    assert result["diagnosis_report"]["evidence_refs"][1]["id"] == "slice_1"
    assert result["diagnosis_report"]["freshness"]["workspace_head_commit"] == "abc123"
    assert fake.awareness_calls == [
        {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
        }
    ]
    assert fake.calls == [
        {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "bug_report_id": "bug_1",
            "status": "final",
            "confidence": "high",
            "root_cause": "OrderController dereferences a missing customer relation.",
            "mechanism": "The show action assumes customer is loaded and calls active().",
            "evidence_refs": [
                {"type": "bug_evidence", "id": "evidence_1"},
                {"type": "source_slice", "id": "slice_1"},
            ],
            "freshness": {
                "status": "current",
                "workspace_head_commit": "abc123",
                "artifact_head_commit": "abc123",
                "index_status": "live_query",
            },
            "payload": {"next_verification": "Run OrderControllerTest::test_show_missing_customer"},
            "redactions": 2,
        }
    ]
    assert fake.closed == 2
    assert timeouts == [0.75, 2.0]


def test_hades_backend_diagnosis_report_create_tool_requires_root_cause(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_diagnosis_report_create",
            {"confidence": "insufficient"},
        )
    )

    assert result["error"] == "Missing required parameter: root_cause"


def test_hades_backend_diagnosis_report_create_tool_blocks_precise_stale_claim(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_diagnosis_report_create",
            {
                "confidence": "high",
                "root_cause": "OrderController dereferences a stale relation.",
                "evidence_refs": [{"type": "bug_evidence", "id": "evidence_1"}],
                "freshness": {"status": "stale"},
            },
        )
    )

    assert result["error"] == "High/medium confidence diagnosis reports require freshness.status=current."
    assert result["freshness_status"] == "stale"


def test_hades_backend_diagnosis_report_create_tool_requires_evidence_for_precise_claim(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_diagnosis_report_create",
            {
                "confidence": "medium",
                "root_cause": "OrderController dereferences a stale relation.",
                "freshness": {"status": "current"},
            },
        )
    )

    assert result["error"] == "High/medium confidence diagnosis reports require evidence_refs."
    assert result["required_for_confidence"] == "medium"


def test_hades_backend_diagnosis_report_create_tool_blocks_incomplete_awareness(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_diagnosis_report_create",
            {
                "confidence": "high",
                "root_cause": "OrderController dereferences a nullable relation.",
                "evidence_refs": [{"type": "bug_evidence", "id": "evidence_1"}],
                "freshness": {"status": "current"},
                "awareness": {"diagnosable_without_source": False},
            },
        )
    )

    assert result["error"] == (
        "High/medium confidence diagnosis reports require "
        "awareness.diagnosable_without_source=true."
    )
    assert result["diagnosable_without_source"] is False


def test_hades_backend_diagnosis_report_create_tool_uses_live_awareness_gate(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)

    class FakeClient:
        def __init__(self):
            self.create_calls = []

        def project_awareness_status(self, **payload):
            return {
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "overall_status": "stale",
                "diagnosable_without_source": True,
                "freshness": {
                    "status": "stale",
                    "workspace_head_commit": "new-head",
                    "artifact_head_commit": "old-head",
                    "index_status": "live_query",
                    "stale_reason": "workspace_head_changed",
                },
                "coverage": {"code_graph": {"status": "stale", "count": 1}},
                "actions": ["Run `hades backend sync` before precise diagnosis."],
            }

        def create_diagnosis_report(self, **payload):
            self.create_calls.append(payload)
            raise AssertionError("stale awareness must block before report create")

        def close(self):
            pass

    fake = FakeClient()
    import plugins.memory.hades_backend as hades_memory

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", lambda *, timeout=None: fake)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_diagnosis_report_create",
            {
                "confidence": "high",
                "root_cause": "OrderController dereferences a nullable relation.",
                "evidence_refs": [{"type": "bug_evidence", "id": "evidence_1"}],
                "freshness": {"status": "current", "workspace_head_commit": "claimed-current"},
                "awareness": {"diagnosable_without_source": True},
            },
        )
    )

    assert result["error"] == "High/medium confidence diagnosis reports require live freshness.status=current."
    assert result["freshness_status"] == "stale"
    assert result["freshness"]["stale_reason"] == "workspace_head_changed"
    assert result["actions"] == ["Run `hades backend sync` before precise diagnosis."]
    assert fake.create_calls == []


def test_hades_backend_resolved_bug_promote_tool_persists_live_backend(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)
    timeouts = []

    class FakeClient:
        def __init__(self):
            self.calls = []
            self.closed = 0

        def promote_diagnosis_report(self, diagnosis_report_id, **payload):
            self.calls.append((diagnosis_report_id, payload))
            return {
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "diagnosis_report_id": diagnosis_report_id,
                "already_promoted": False,
                "server_time": "2026-07-07T12:30:00Z",
                "resolved_bug_memory": {
                    "id": "mem_bug_1",
                    "kind": "resolved_bug",
                    "summary": "Resolved bug: active() on null in OrderController.",
                    "payload": {
                        "schema": "hades.resolved_bug.v1",
                        "root_cause": "OrderController dereferences a missing customer relation.",
                        "verification_status": payload["verification_status"],
                        "affected_symbols": payload["affected_symbols"],
                    },
                    "occurred_at": "2026-07-07T12:29:00Z",
                    "updated_at": "2026-07-07T12:29:00Z",
                    "version": "mem_resolved_bug_1",
                },
            }

        def close(self):
            self.closed += 1

    fake = FakeClient()
    import plugins.memory.hades_backend as hades_memory

    def client_from_config(*, timeout=None):
        timeouts.append(timeout)
        return fake

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", client_from_config)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_resolved_bug_promote",
            {
                "diagnosis_report_id": "diag_1",
                "verification_status": "test_passed",
                "fix_commit": "abc123",
                "affected_symbols": ["OrderController@show"],
                "regression_tests": ["OrderControllerTest::test_show_missing_customer"],
                "payload": {"notes": "Focused regression test passed."},
                "redactions": 1,
            },
        )
    )

    assert result["status"] == "ok"
    assert result["resolved_bug_memory"]["id"] == "mem_bug_1"
    assert result["resolved_bug_memory"]["kind"] == "resolved_bug"
    assert result["resolved_bug_memory"]["payload"]["verification_status"] == "test_passed"
    assert fake.calls == [
        (
            "diag_1",
            {
                "project_id": "proj_1",
                "workspace_binding_id": "wb_1",
                "verification_status": "test_passed",
                "fix_commit": "abc123",
                "fix_pr_url": None,
                "affected_symbols": ["OrderController@show"],
                "regression_tests": ["OrderControllerTest::test_show_missing_customer"],
                "payload": {"notes": "Focused regression test passed."},
                "redactions": 1,
            },
        )
    ]
    assert fake.closed == 1
    assert timeouts == [2.0]


def test_hades_backend_resolved_bug_promote_tool_requires_verification(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_resolved_bug_promote",
            {"diagnosis_report_id": "diag_1", "verification_status": "guessed"},
        )
    )

    assert result["error"].startswith("Unsupported resolved bug verification status")


def test_hades_backend_project_awareness_status_tool_reads_live_backend(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)

    class FakeClient:
        def __init__(self):
            self.calls = []
            self.closed = 0

        def project_awareness_status(self, **payload):
            self.calls.append(payload)
            return {
                "protocol_version": "v1",
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "workspace_head_commit": "abc123",
                "overall_status": "partial",
                "diagnosable_without_source": False,
                "freshness": {
                    "status": "current",
                    "workspace_head_commit": "abc123",
                    "artifact_head_commit": "abc123",
                    "index_status": "live_query",
                    "stale_reason": None,
                },
                "coverage": {
                    "memory": {"status": "current", "count": 2},
                    "artifacts": {"status": "current", "count": 1},
                    "bug_evidence": {"status": "missing", "count": 0},
                    "code_graph": {"status": "partial", "count": 1},
                    "source_slices": {"status": "missing", "count": 0},
                },
                "actions": ["Capture typed bug evidence before precise root-cause claims."],
                "server_time": "2026-07-07T12:00:00Z",
            }

        def close(self):
            self.closed += 1

    fake = FakeClient()
    import plugins.memory.hades_backend as hades_memory

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", lambda *, timeout=None: fake)

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_project_awareness_status",
            {},
        )
    )

    assert result["status"] == "ok"
    assert result["overall_status"] == "partial"
    assert result["diagnosable_without_source"] is False
    assert result["freshness"]["status"] == "current"
    assert result["coverage"]["code_graph"]["status"] == "partial"
    assert "Capture typed bug evidence" in result["actions"][0]
    assert fake.calls == [
        {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
        }
    ]
    assert fake.closed == 1


def test_hades_backend_project_awareness_status_tool_uses_short_timeout(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)
    timeouts = []

    class FakeClient:
        def project_awareness_status(self, **payload):
            return {
                "project_id": payload["project_id"],
                "workspace_binding_id": payload["workspace_binding_id"],
                "overall_status": "missing_index",
                "diagnosable_without_source": False,
            }

        def close(self):
            pass

    import plugins.memory.hades_backend as hades_memory

    def client_from_config(*, timeout=None):
        timeouts.append(timeout)
        return FakeClient()

    monkeypatch.setattr(hades_memory.runtime, "client_from_config", client_from_config)

    provider.handle_tool_call("hades_backend_project_awareness_status", {})

    assert timeouts == [0.75]


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


def test_hades_backend_memory_search_ranks_verified_backfill_fact_before_raw_chunk(monkeypatch, tmp_path):
    provider = _create_linked_provider(
        monkeypatch,
        tmp_path,
        items=[
            {
                "id": "chunk_1",
                "domain": "source_chunks",
                "schema": "hades.backend_wiki.file_chunk.v1",
                "path": "graphify-sidecar/carnovali-facts.md",
                "summary": (
                    "taxonomy route taxonomy route taxonomy route "
                    "SecurityActivityCategoryController handled_by extracted chunk"
                ),
            },
            {
                "id": "fact_1",
                "domain": "project_memory",
                "kind": "verified_note_fact",
                "schema": "hades.verified_note_fact.v1",
                "source": "note_backfill_candidate",
                "summary": (
                    "Verified route taxonomy_flock_vocabulary_security_activity_category_show "
                    "is handled by SecurityActivityCategoryController."
                ),
                "payload": {
                    "route": "taxonomy_flock_vocabulary_security_activity_category_show",
                    "handler": "SecurityActivityCategoryController",
                    "workspace_head_commit": "abc123",
                    "index_status": "reviewed_note_fact",
                },
            },
        ],
    )

    result = json.loads(
        provider.handle_tool_call(
            "hades_backend_project_memory_search",
            {
                "query": "taxonomy route SecurityActivityCategoryController",
                "include_raw_chunks": True,
                "limit": 2,
            },
        )
    )

    assert result["status"] == "ok"
    assert result["raw_chunks_omitted"] == 0
    assert [item["id"] for item in result["items"]] == ["fact_1", "chunk_1"]
    assert result["items"][0]["kind"] == "verified_note_fact"
    assert result["items"][0]["raw_chunk"] is False
    assert result["items"][1]["raw_chunk"] is True
    assert result["items"][0]["score"] > result["items"][1]["score"]


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
    bug_result = json.loads(
        provider.handle_tool_call(
            "hades_backend_bug_evidence_search",
            {"query": "stack trace"},
        )
    )
    traversal_result = json.loads(
        provider.handle_tool_call(
            "hades_backend_graph_traverse",
            {"start": "orders.show"},
        )
    )
    awareness_result = json.loads(
        provider.handle_tool_call(
            "hades_backend_project_awareness_status",
            {},
        )
    )
    diagnosis_result = json.loads(
        provider.handle_tool_call(
            "hades_backend_diagnosis_report_create",
            {"confidence": "insufficient", "root_cause": "not determined"},
        )
    )
    promote_result = json.loads(
        provider.handle_tool_call(
            "hades_backend_resolved_bug_promote",
            {"diagnosis_report_id": "diag_1", "verification_status": "manual_review"},
        )
    )

    assert "not linked" in block
    assert result["status"] == "unmapped_project"
    assert result["items"] == []
    assert bug_result["status"] == "unmapped_project"
    assert bug_result["items"] == []
    assert traversal_result["status"] == "unmapped_project"
    assert traversal_result["nodes"] == []
    assert traversal_result["edges"] == []
    assert awareness_result["status"] == "unmapped_project"
    assert diagnosis_result["status"] == "unmapped_project"
    assert promote_result["status"] == "unmapped_project"


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
