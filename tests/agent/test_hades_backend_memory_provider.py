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
            },
            {
                "method": "PUT",
                "uri": "/invoices/{invoice}",
                "name": "invoices.update",
                "handler": "InvoiceController@update",
                "resource": "invoices",
                "resource_action": "update",
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
                "kind": "method",
                "name": "OrderService@format",
                "class": "App\\Services\\OrderService",
                "method": "format",
                "role": "service",
                "path": "app/Services/OrderService.php",
                "line": 5,
            },
            {
                "kind": "method",
                "name": "SyncOrderJob@handle",
                "class": "App\\Jobs\\SyncOrderJob",
                "method": "handle",
                "role": "job",
                "path": "app/Jobs/SyncOrderJob.php",
                "line": 4,
            },
            {
                "kind": "method",
                "name": "OrderPolicy@view",
                "class": "App\\Policies\\OrderPolicy",
                "method": "view",
                "role": "policy",
                "path": "app/Policies/OrderPolicy.php",
                "line": 4,
            },
            {
                "kind": "method",
                "name": "SendOrderReceipt@handle",
                "class": "App\\Listeners\\SendOrderReceipt",
                "method": "handle",
                "role": "listener",
                "path": "app/Listeners/SendOrderReceipt.php",
                "line": 4,
            },
            {
                "kind": "method",
                "name": "OrderReceiptMail@build",
                "class": "App\\Mail\\OrderReceiptMail",
                "method": "build",
                "role": "mailable",
                "path": "app/Mail/OrderReceiptMail.php",
                "line": 4,
            },
            {
                "kind": "method",
                "name": "OrderShippedNotification@toMail",
                "class": "App\\Notifications\\OrderShippedNotification",
                "method": "toMail",
                "role": "notification",
                "path": "app/Notifications/OrderShippedNotification.php",
                "line": 4,
            },
            {
                "kind": "method",
                "name": "SyncOrdersCommand@handle",
                "class": "App\\Console\\Commands\\SyncOrdersCommand",
                "method": "handle",
                "role": "artisan_command",
                "path": "app/Console/Commands/SyncOrdersCommand.php",
                "line": 6,
            },
            {
                "kind": "class",
                "name": "App\\Exceptions\\OrderLockedException",
                "short_name": "OrderLockedException",
                "role": "php_class",
                "path": "app/Exceptions/OrderLockedException.php",
                "line": 3,
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
                "kind": "route_handler",
                "from": "route:invoices.update",
                "to": "InvoiceController@update",
                "method": "PUT",
                "uri": "/invoices/{invoice}",
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
                "kind": "request_authorization",
                "from": "App\\Http\\Requests\\StoreOrderRequest",
                "to": "authorization:form_request",
                "authorization_result": "deny",
                "authorization_path": "app/Http/Requests/StoreOrderRequest.php",
                "authorization_line": 8,
                "path": "app/Http/Requests/StoreOrderRequest.php",
                "line": 8,
            },
            {
                "kind": "route_request_authorization",
                "from": "route:orders.show",
                "to": "App\\Http\\Requests\\StoreOrderRequest",
                "handler": "OrderController@show",
                "param": "request",
                "authorization_result": "deny",
                "authorization_path": "app/Http/Requests/StoreOrderRequest.php",
                "authorization_line": 8,
                "method": "GET",
                "uri": "/orders/{order}",
                "path": "routes/web.php",
                "line": 4,
            },
            {
                "kind": "request_input_mutation",
                "from": "App\\Http\\Requests\\StoreOrderRequest",
                "to": "request_field:status",
                "field": "status",
                "operation": "merge",
                "mutation_stage": "prepare_for_validation",
                "mutation_path": "app/Http/Requests/StoreOrderRequest.php",
                "mutation_line": 10,
                "path": "app/Http/Requests/StoreOrderRequest.php",
                "line": 10,
            },
            {
                "kind": "route_request_input_mutation",
                "from": "route:orders.show",
                "to": "request_field:status",
                "request_class": "App\\Http\\Requests\\StoreOrderRequest",
                "handler": "OrderController@show",
                "param": "request",
                "field": "status",
                "operation": "merge",
                "mutation_stage": "prepare_for_validation",
                "mutation_path": "app/Http/Requests/StoreOrderRequest.php",
                "mutation_line": 10,
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
                "kind": "route_validation_database_rule",
                "from": "route:orders.show",
                "to": "table:customers.id",
                "field": "customer_id",
                "rule": "exists",
                "table": "customers",
                "column": "id",
                "request_class": "App\\Http\\Requests\\StoreOrderRequest",
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
                "kind": "validation_database_rule",
                "from": "App\\Http\\Requests\\StoreOrderRequest",
                "to": "table:customers.id",
                "field": "customer_id",
                "rule": "exists",
                "table": "customers",
                "column": "id",
                "path": "app/Http/Requests/StoreOrderRequest.php",
                "line": 6,
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
                "kind": "authorization_policy_method",
                "from": "OrderController@show",
                "to": "OrderPolicy@view",
                "ability": "view",
                "policy_class": "App\\Policies\\OrderPolicy",
                "target_model": "App\\Models\\Order",
                "table": "orders",
                "path": "app/Http/Controllers/OrderController.php",
                "line": 13,
            },
            {
                "kind": "route_authorization_policy_method",
                "from": "route:orders.show",
                "to": "OrderPolicy@view",
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
                "policy_class": "App\\Policies\\OrderPolicy",
            },
            {
                "kind": "policy_for",
                "from": "App\\Models\\Order",
                "to": "App\\Policies\\OrderPolicy",
                "source": "policies_property",
                "path": "app/Providers/AuthServiceProvider.php",
                "line": 12,
            },
            {
                "kind": "http_abort",
                "from": "OrderController@show",
                "to": "http_status:403",
                "status_code": 403,
                "abort_helper": "abort_if",
                "path": "app/Http/Controllers/OrderController.php",
                "line": 44,
            },
            {
                "kind": "route_http_abort",
                "from": "route:orders.show",
                "to": "http_status:403",
                "handler": "OrderController@show",
                "status_code": 403,
                "abort_helper": "abort_if",
                "method": "GET",
                "uri": "/orders/{order}",
                "path": "routes/web.php",
                "line": 4,
                "source_path": "app/Http/Controllers/OrderController.php",
                "source_line": 44,
            },
            {
                "kind": "calls_method",
                "from": "OrderController@show",
                "to": "OrderService@format",
                "target_class": "App\\Services\\OrderService",
                "call_type": "static",
                "target_method": "format",
                "path": "app/Http/Controllers/OrderController.php",
                "line": 44,
            },
            {
                "kind": "calls_method",
                "from": "OrderController@show",
                "to": "OrderService@format",
                "target_class": "App\\Services\\OrderService",
                "call_type": "instance",
                "receiver": "formatter",
                "target_method": "format",
                "abstract_class": "App\\Contracts\\OrderFormatter",
                "binding": "singleton",
                "path": "app/Http/Controllers/OrderController.php",
                "line": 45,
            },
            {
                "kind": "calls_method",
                "from": "OrderController@show",
                "to": "OrderService@format",
                "target_class": "App\\Services\\OrderService",
                "call_type": "property",
                "receiver": "orders",
                "target_method": "format",
                "path": "app/Http/Controllers/OrderController.php",
                "line": 46,
            },
            {
                "kind": "throws_exception",
                "from": "OrderService@format",
                "to": "App\\Exceptions\\OrderLockedException",
                "exception_class": "App\\Exceptions\\OrderLockedException",
                "exception_short_name": "OrderLockedException",
                "path": "app/Services/OrderService.php",
                "line": 5,
            },
            {
                "kind": "http_response_status",
                "from": "InvoiceController@update",
                "to": "http_status:409",
                "status_code": 409,
                "response_helper": "response_json",
                "path": "app/Http/Controllers/InvoiceController.php",
                "line": 7,
            },
            {
                "kind": "route_http_response_status",
                "from": "route:invoices.update",
                "to": "http_status:409",
                "handler": "InvoiceController@update",
                "status_code": 409,
                "response_helper": "response_json",
                "method": "PUT",
                "uri": "/invoices/{invoice}",
                "path": "routes/web.php",
                "line": 5,
                "source_path": "app/Http/Controllers/InvoiceController.php",
                "source_line": 7,
            },
            {
                "kind": "dispatches_job",
                "from": "OrderController@show",
                "to": "App\\Jobs\\SyncOrderJob",
                "path": "app/Http/Controllers/OrderController.php",
                "line": 41,
            },
            {
                "kind": "dispatches_job_method",
                "from": "OrderController@show",
                "to": "SyncOrderJob@handle",
                "job_class": "App\\Jobs\\SyncOrderJob",
                "job_method": "handle",
                "dispatch_method": "dispatch",
                "path": "app/Http/Controllers/OrderController.php",
                "line": 41,
            },
            {
                "kind": "route_dispatches_job_method",
                "from": "route:orders.show",
                "to": "SyncOrderJob@handle",
                "handler": "OrderController@show",
                "job_class": "App\\Jobs\\SyncOrderJob",
                "job_method": "handle",
                "dispatch_method": "dispatch",
                "method": "GET",
                "uri": "/orders/{order}",
                "path": "routes/web.php",
                "line": 4,
                "source_path": "app/Http/Controllers/OrderController.php",
                "source_line": 41,
            },
            {
                "kind": "artisan_command",
                "from": "App\\Console\\Commands\\SyncOrdersCommand",
                "to": "command:orders:sync",
                "path": "app/Console/Commands/SyncOrdersCommand.php",
                "line": 5,
            },
            {
                "kind": "artisan_command_method",
                "from": "command:orders:sync",
                "to": "SyncOrdersCommand@handle",
                "command_class": "App\\Console\\Commands\\SyncOrdersCommand",
                "command_method": "handle",
                "path": "app/Console/Commands/SyncOrdersCommand.php",
                "line": 5,
            },
            {
                "kind": "scheduled_command",
                "from": "App\\Console\\Kernel",
                "to": "command:orders:sync",
                "cadence": "hourly",
                "path": "app/Console/Kernel.php",
                "line": 6,
            },
            {
                "kind": "scheduled_command_method",
                "from": "App\\Console\\Kernel",
                "to": "SyncOrdersCommand@handle",
                "command": "command:orders:sync",
                "command_class": "App\\Console\\Commands\\SyncOrdersCommand",
                "command_method": "handle",
                "cadence": "hourly",
                "path": "app/Console/Kernel.php",
                "line": 6,
            },
            {
                "kind": "scheduled_job",
                "from": "App\\Console\\Kernel",
                "to": "App\\Jobs\\SyncOrderJob",
                "cadence": "daily",
                "path": "app/Console/Kernel.php",
                "line": 7,
            },
            {
                "kind": "scheduled_job_method",
                "from": "App\\Console\\Kernel",
                "to": "SyncOrderJob@handle",
                "job_class": "App\\Jobs\\SyncOrderJob",
                "job_method": "handle",
                "cadence": "daily",
                "path": "app/Console/Kernel.php",
                "line": 7,
            },
            {
                "kind": "emits_event",
                "from": "OrderController@show",
                "to": "App\\Events\\OrderPlaced",
                "path": "app/Http/Controllers/OrderController.php",
                "line": 42,
            },
            {
                "kind": "event_listener",
                "from": "App\\Events\\OrderPlaced",
                "to": "App\\Listeners\\SendOrderReceipt",
                "path": "app/Providers/AuthServiceProvider.php",
                "line": 13,
            },
            {
                "kind": "event_listener_method",
                "from": "App\\Events\\OrderPlaced",
                "to": "SendOrderReceipt@handle",
                "listener_class": "App\\Listeners\\SendOrderReceipt",
                "listener_method": "handle",
                "path": "app/Providers/AuthServiceProvider.php",
                "line": 13,
            },
            {
                "kind": "emits_event_listener",
                "from": "OrderController@show",
                "to": "SendOrderReceipt@handle",
                "event_class": "App\\Events\\OrderPlaced",
                "listener_class": "App\\Listeners\\SendOrderReceipt",
                "listener_path": "app/Providers/AuthServiceProvider.php",
                "listener_line": 13,
                "path": "app/Http/Controllers/OrderController.php",
                "line": 42,
            },
            {
                "kind": "route_emits_event_listener",
                "from": "route:orders.show",
                "to": "SendOrderReceipt@handle",
                "handler": "OrderController@show",
                "event_class": "App\\Events\\OrderPlaced",
                "listener_class": "App\\Listeners\\SendOrderReceipt",
                "method": "GET",
                "uri": "/orders/{order}",
                "path": "routes/web.php",
                "line": 4,
                "source_path": "app/Http/Controllers/OrderController.php",
                "source_line": 42,
                "listener_path": "app/Providers/AuthServiceProvider.php",
                "listener_line": 13,
            },
            {
                "kind": "sends_mail",
                "from": "OrderController@show",
                "to": "App\\Mail\\OrderReceiptMail",
                "mail_method": "send",
                "path": "app/Http/Controllers/OrderController.php",
                "line": 42,
            },
            {
                "kind": "sends_mail_method",
                "from": "OrderController@show",
                "to": "OrderReceiptMail@build",
                "mailable_class": "App\\Mail\\OrderReceiptMail",
                "mailable_method": "build",
                "mail_method": "send",
                "path": "app/Http/Controllers/OrderController.php",
                "line": 42,
            },
            {
                "kind": "route_sends_mail_method",
                "from": "route:orders.show",
                "to": "OrderReceiptMail@build",
                "handler": "OrderController@show",
                "mailable_class": "App\\Mail\\OrderReceiptMail",
                "mailable_method": "build",
                "mail_method": "send",
                "method": "GET",
                "uri": "/orders/{order}",
                "path": "routes/web.php",
                "line": 4,
                "source_path": "app/Http/Controllers/OrderController.php",
                "source_line": 42,
            },
            {
                "kind": "sends_notification",
                "from": "OrderController@show",
                "to": "App\\Notifications\\OrderShippedNotification",
                "notification_source": "notifiable_notify",
                "path": "app/Http/Controllers/OrderController.php",
                "line": 42,
            },
            {
                "kind": "sends_notification_method",
                "from": "OrderController@show",
                "to": "OrderShippedNotification@toMail",
                "notification_class": "App\\Notifications\\OrderShippedNotification",
                "notification_method": "toMail",
                "notification_source": "notifiable_notify",
                "path": "app/Http/Controllers/OrderController.php",
                "line": 42,
            },
            {
                "kind": "route_sends_notification_method",
                "from": "route:orders.show",
                "to": "OrderShippedNotification@toMail",
                "handler": "OrderController@show",
                "notification_class": "App\\Notifications\\OrderShippedNotification",
                "notification_method": "toMail",
                "notification_source": "notifiable_notify",
                "method": "GET",
                "uri": "/orders/{order}",
                "path": "routes/web.php",
                "line": 4,
                "source_path": "app/Http/Controllers/OrderController.php",
                "source_line": 42,
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


def test_hades_backend_graph_search_finds_local_query_modifier_edges(monkeypatch, tmp_path):
    graph_payload = _php_graph_artifact()
    graph_payload["edges"].extend(
        [
            {
                "kind": "query_operation",
                "from": "OrderController@show",
                "to": "query:orders:withTrashed",
                "table": "orders",
                "model": "App\\Models\\Order",
                "operation": "withTrashed",
                "access": "scope",
                "path": "app/Http/Controllers/OrderController.php",
                "line": 50,
            },
            {
                "kind": "query_operation",
                "from": "OrderController@show",
                "to": "query:orders:lockForUpdate",
                "table": "orders",
                "model": "App\\Models\\Order",
                "operation": "lockForUpdate",
                "access": "lock",
                "path": "app/Http/Controllers/OrderController.php",
                "line": 51,
            },
        ]
    )

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
                "payload": graph_payload,
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
            {"query": "orders withTrashed lockForUpdate scope lock", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "query_operation"
        and ref["to"] == "query:orders:withTrashed"
        for ref in graph_refs
    )
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "query_operation"
        and ref["to"] == "query:orders:lockForUpdate"
        for ref in graph_refs
    )
    assert any(
        "operation=withTrashed" in item["summary"]
        and "access=scope" in item["summary"]
        and "table=orders" in item["summary"]
        for item in result["items"]
    )
    assert any(
        "operation=lockForUpdate" in item["summary"]
        and "access=lock" in item["summary"]
        and "model=App\\Models\\Order" in item["summary"]
        for item in result["items"]
    )


def test_hades_backend_graph_search_finds_local_model_instance_operation_edges(monkeypatch, tmp_path):
    graph_payload = _php_graph_artifact()
    graph_payload["edges"].append(
        {
            "kind": "route_model_instance_operation",
            "from": "route:orders.show",
            "to": "model_operation:orders:restore",
            "model": "App\\Models\\Order",
            "table": "orders",
            "operation": "restore",
            "access": "restore",
            "receiver": "order",
            "source_path": "app/Http/Controllers/OrderController.php",
            "source_line": 43,
            "handler": "OrderController@show",
            "method": "GET",
            "uri": "/orders/{order}",
            "path": "routes/web.php",
            "line": 4,
        }
    )
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
                "payload": graph_payload,
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
            {"query": "orders route restore model instance", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "route_model_instance_operation"
        and ref["from"] == "route:orders.show"
        and ref["to"] == "model_operation:orders:restore"
        for ref in graph_refs
    )
    assert any(
        "operation=restore" in item["summary"]
        and "access=restore" in item["summary"]
        and "receiver=order" in item["summary"]
        for item in result["items"]
    )


def test_hades_backend_graph_search_finds_local_method_call_edges(monkeypatch, tmp_path):
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
            {"query": "OrderService format static call", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "calls_method"
        and ref["to"] == "OrderService@format"
        and ref["provenance"]["target_class"] == "App\\Services\\OrderService"
        for ref in graph_refs
    )
    assert any(
        "call_type=static" in item["summary"]
        and "target_method=format" in item["summary"]
        and "target_class=App\\Services\\OrderService" in item["summary"]
        for item in result["items"]
    )


def test_hades_backend_graph_search_finds_local_instance_method_call_edges(monkeypatch, tmp_path):
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
            {"query": "formatter instance OrderService format", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "calls_method"
        and ref["to"] == "OrderService@format"
        and ref["provenance"]["call_type"] == "instance"
        and ref["provenance"]["receiver"] == "formatter"
        and ref["provenance"]["abstract_class"] == "App\\Contracts\\OrderFormatter"
        and ref["provenance"]["binding"] == "singleton"
        for ref in graph_refs
    )
    assert any(
        "call_type=instance" in item["summary"]
        and "target_method=format" in item["summary"]
        and "receiver=formatter" in item["summary"]
        and "abstract_class=App\\Contracts\\OrderFormatter" in item["summary"]
        and "binding=singleton" in item["summary"]
        for item in result["items"]
    )


def test_hades_backend_graph_search_finds_local_property_method_call_edges(monkeypatch, tmp_path):
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
            {"query": "orders property OrderService format", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "calls_method"
        and ref["to"] == "OrderService@format"
        and ref["provenance"]["call_type"] == "property"
        and ref["provenance"]["receiver"] == "orders"
        for ref in graph_refs
    )
    assert any(
        "call_type=property" in item["summary"]
        and "target_method=format" in item["summary"]
        and "receiver=orders" in item["summary"]
        for item in result["items"]
    )


def test_hades_backend_graph_search_finds_local_exception_throw_edges(monkeypatch, tmp_path):
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
            {"query": "OrderLockedException throws service", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "throws_exception"
        and ref["from"] == "OrderService@format"
        and ref["to"] == "App\\Exceptions\\OrderLockedException"
        for ref in graph_refs
    )
    assert any(
        "exception_class=App\\Exceptions\\OrderLockedException" in item["summary"]
        and "exception_short_name=OrderLockedException" in item["summary"]
        for item in result["items"]
    )


def test_hades_backend_graph_search_finds_local_http_response_status_edges(monkeypatch, tmp_path):
    provider = _create_linked_provider(
        monkeypatch,
        tmp_path,
        items=[
            {
                "id": "artifact_1",
                "domain": "artifacts",
                "schema": "hades.php_graph.v1",
                "source": "hades.php_graph.v1",
                "summary": "Laravel graph artifact for invoice route.",
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
            {"query": "invoices 409 response_json", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "route_http_response_status"
        and ref["from"] == "route:invoices.update"
        and ref["to"] == "http_status:409"
        for ref in graph_refs
    )
    assert any(
        "status_code=409" in item["summary"]
        and "response_helper=response_json" in item["summary"]
        and "handler=InvoiceController@update" in item["summary"]
        for item in result["items"]
    )


def test_hades_backend_graph_search_finds_local_http_redirect_edges(monkeypatch, tmp_path):
    artifact = _php_graph_artifact()
    artifact["edges"].extend(
        [
            {
                "kind": "http_redirect",
                "from": "OrderController@show",
                "to": "redirect_route:orders.index",
                "redirect_type": "route",
                "redirect_target": "orders.index",
                "redirect_helper": "redirect_route",
                "redirect_status": 302,
                "path": "app/Http/Controllers/OrderController.php",
                "line": 45,
            },
            {
                "kind": "route_http_redirect",
                "from": "route:orders.show",
                "to": "redirect_route:orders.index",
                "handler": "OrderController@show",
                "redirect_type": "route",
                "redirect_target": "orders.index",
                "redirect_helper": "redirect_route",
                "redirect_status": 302,
                "method": "GET",
                "uri": "/orders/{order}",
                "path": "routes/web.php",
                "line": 4,
                "source_path": "app/Http/Controllers/OrderController.php",
                "source_line": 45,
            },
        ]
    )
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
                "payload": artifact,
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
            {"query": "orders redirect route orders.index 302", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "route_http_redirect"
        and ref["from"] == "route:orders.show"
        and ref["to"] == "redirect_route:orders.index"
        and ref["provenance"]["redirect_type"] == "route"
        and ref["provenance"]["redirect_target"] == "orders.index"
        and ref["provenance"]["redirect_status"] == 302
        for ref in graph_refs
    )
    assert any(
        "redirect_type=route" in item["summary"]
        and "redirect_target=orders.index" in item["summary"]
        and "redirect_status=302" in item["summary"]
        for item in result["items"]
    )


def test_hades_backend_graph_search_finds_local_session_access_edges(monkeypatch, tmp_path):
    artifact = _php_graph_artifact()
    artifact["edges"].extend(
        [
            {
                "kind": "session_access",
                "from": "OrderController@show",
                "to": "session_key:orders.notice",
                "session_key": "orders.notice",
                "session_operation": "flash",
                "session_method": "session_flash",
                "path": "app/Http/Controllers/OrderController.php",
                "line": 45,
            },
            {
                "kind": "route_session_access",
                "from": "route:orders.show",
                "to": "session_key:orders.notice",
                "handler": "OrderController@show",
                "session_key": "orders.notice",
                "session_operation": "flash",
                "session_method": "session_flash",
                "method": "GET",
                "uri": "/orders/{order}",
                "path": "routes/web.php",
                "line": 4,
                "source_path": "app/Http/Controllers/OrderController.php",
                "source_line": 45,
            },
        ]
    )
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
                "payload": artifact,
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
            {"query": "orders session flash orders.notice", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "route_session_access"
        and ref["from"] == "route:orders.show"
        and ref["to"] == "session_key:orders.notice"
        and ref["provenance"]["session_key"] == "orders.notice"
        and ref["provenance"]["session_operation"] == "flash"
        for ref in graph_refs
    )
    assert any(
        "session_key=orders.notice" in item["summary"]
        and "session_operation=flash" in item["summary"]
        and "session_method=session_flash" in item["summary"]
        for item in result["items"]
    )


def test_hades_backend_graph_search_finds_local_cache_access_edges(monkeypatch, tmp_path):
    artifact = _php_graph_artifact()
    artifact["edges"].extend(
        [
            {
                "kind": "cache_access",
                "from": "OrderController@show",
                "to": "cache_key:orders.summary",
                "cache_key": "orders.summary",
                "cache_operation": "read_write",
                "cache_method": "cache_remember",
                "cache_ttl_present": True,
                "path": "app/Http/Controllers/OrderController.php",
                "line": 45,
            },
            {
                "kind": "route_cache_access",
                "from": "route:orders.show",
                "to": "cache_key:orders.summary",
                "handler": "OrderController@show",
                "cache_key": "orders.summary",
                "cache_operation": "read_write",
                "cache_method": "cache_remember",
                "cache_ttl_present": True,
                "method": "GET",
                "uri": "/orders/{order}",
                "path": "routes/web.php",
                "line": 4,
                "source_path": "app/Http/Controllers/OrderController.php",
                "source_line": 45,
            },
        ]
    )
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
                "payload": artifact,
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
            {"query": "orders cache remember orders.summary", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "route_cache_access"
        and ref["from"] == "route:orders.show"
        and ref["to"] == "cache_key:orders.summary"
        and ref["provenance"]["cache_key"] == "orders.summary"
        and ref["provenance"]["cache_operation"] == "read_write"
        and ref["provenance"]["cache_ttl_present"] is True
        for ref in graph_refs
    )
    assert any(
        "cache_key=orders.summary" in item["summary"]
        and "cache_operation=read_write" in item["summary"]
        and "cache_ttl_present=True" in item["summary"]
        for item in result["items"]
    )


def test_hades_backend_graph_search_finds_local_outbound_http_call_edges(monkeypatch, tmp_path):
    artifact = _php_graph_artifact()
    artifact["edges"].extend(
        [
            {
                "kind": "outbound_http_call",
                "from": "OrderController@show",
                "to": "http_endpoint:api.example.test/orders/sync",
                "http_client": "laravel_http",
                "http_method": "POST",
                "http_scheme": "https",
                "http_host": "api.example.test",
                "http_path": "/orders/sync",
                "http_call_method": "Http::post",
                "path": "app/Http/Controllers/OrderController.php",
                "line": 46,
            },
            {
                "kind": "route_outbound_http_call",
                "from": "route:orders.show",
                "to": "http_endpoint:api.example.test/orders/sync",
                "handler": "OrderController@show",
                "http_client": "laravel_http",
                "http_method": "POST",
                "http_scheme": "https",
                "http_host": "api.example.test",
                "http_path": "/orders/sync",
                "http_call_method": "Http::post",
                "method": "GET",
                "uri": "/orders/{order}",
                "path": "routes/web.php",
                "line": 4,
                "source_path": "app/Http/Controllers/OrderController.php",
                "source_line": 46,
            },
        ]
    )
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
                "payload": artifact,
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
            {"query": "orders outbound http api.example.test POST sync", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "route_outbound_http_call"
        and ref["from"] == "route:orders.show"
        and ref["to"] == "http_endpoint:api.example.test/orders/sync"
        and ref["provenance"]["http_method"] == "POST"
        and ref["provenance"]["http_host"] == "api.example.test"
        and ref["provenance"]["http_path"] == "/orders/sync"
        for ref in graph_refs
    )
    assert any(
        "http_method=POST" in item["summary"]
        and "http_host=api.example.test" in item["summary"]
        and "http_path=/orders/sync" in item["summary"]
        for item in result["items"]
    )


def test_hades_backend_graph_search_finds_local_storage_access_edges(monkeypatch, tmp_path):
    artifact = _php_graph_artifact()
    artifact["edges"].extend(
        [
            {
                "kind": "storage_access",
                "from": "OrderController@show",
                "to": "storage_path:s3:orders/export.csv",
                "storage_disk": "s3",
                "storage_path": "orders/export.csv",
                "storage_operation": "write",
                "storage_method": "storage_put",
                "path": "app/Http/Controllers/OrderController.php",
                "line": 47,
            },
            {
                "kind": "route_storage_access",
                "from": "route:orders.show",
                "to": "storage_path:s3:orders/export.csv",
                "handler": "OrderController@show",
                "storage_disk": "s3",
                "storage_path": "orders/export.csv",
                "storage_operation": "write",
                "storage_method": "storage_put",
                "method": "GET",
                "uri": "/orders/{order}",
                "path": "routes/web.php",
                "line": 4,
                "source_path": "app/Http/Controllers/OrderController.php",
                "source_line": 47,
            },
        ]
    )
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
                "payload": artifact,
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
            {"query": "orders storage s3 export write", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "route_storage_access"
        and ref["from"] == "route:orders.show"
        and ref["to"] == "storage_path:s3:orders/export.csv"
        and ref["provenance"]["storage_disk"] == "s3"
        and ref["provenance"]["storage_path"] == "orders/export.csv"
        and ref["provenance"]["storage_operation"] == "write"
        for ref in graph_refs
    )
    assert any(
        "storage_disk=s3" in item["summary"]
        and "storage_path=orders/export.csv" in item["summary"]
        and "storage_operation=write" in item["summary"]
        for item in result["items"]
    )


def test_hades_backend_graph_search_finds_local_request_input_access_edges(monkeypatch, tmp_path):
    artifact = _php_graph_artifact()
    artifact["edges"].extend(
        [
            {
                "kind": "request_input_access",
                "from": "OrderController@show",
                "to": "request_field:customer_note",
                "field": "customer_note",
                "input_source": "input",
                "input_method": "request_input",
                "path": "app/Http/Controllers/OrderController.php",
                "line": 48,
            },
            {
                "kind": "route_request_input_access",
                "from": "route:orders.show",
                "to": "request_field:customer_note",
                "handler": "OrderController@show",
                "field": "customer_note",
                "input_source": "input",
                "input_method": "request_input",
                "method": "GET",
                "uri": "/orders/{order}",
                "path": "routes/web.php",
                "line": 4,
                "source_path": "app/Http/Controllers/OrderController.php",
                "source_line": 48,
            },
        ]
    )
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
                "payload": artifact,
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
            {"query": "orders request input customer_note", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "route_request_input_access"
        and ref["from"] == "route:orders.show"
        and ref["to"] == "request_field:customer_note"
        and ref["provenance"]["field"] == "customer_note"
        and ref["provenance"]["input_source"] == "input"
        and ref["provenance"]["input_method"] == "request_input"
        for ref in graph_refs
    )
    assert any(
        "field=customer_note" in item["summary"]
        and "input_source=input" in item["summary"]
        and "input_method=request_input" in item["summary"]
        for item in result["items"]
    )


def test_hades_backend_graph_search_finds_local_request_file_access_edges(monkeypatch, tmp_path):
    artifact = _php_graph_artifact()
    artifact["edges"].extend(
        [
            {
                "kind": "request_file_access",
                "from": "OrderController@show",
                "to": "request_file:invoice_pdf",
                "file_field": "invoice_pdf",
                "file_operation": "check",
                "file_method": "request_hasfile",
                "path": "app/Http/Controllers/OrderController.php",
                "line": 49,
            },
            {
                "kind": "route_request_file_access",
                "from": "route:orders.show",
                "to": "request_file:invoice_pdf",
                "handler": "OrderController@show",
                "file_field": "invoice_pdf",
                "file_operation": "check",
                "file_method": "request_hasfile",
                "method": "GET",
                "uri": "/orders/{order}",
                "path": "routes/web.php",
                "line": 4,
                "source_path": "app/Http/Controllers/OrderController.php",
                "source_line": 49,
            },
        ]
    )
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
                "payload": artifact,
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
            {"query": "orders upload invoice_pdf hasFile", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "route_request_file_access"
        and ref["from"] == "route:orders.show"
        and ref["to"] == "request_file:invoice_pdf"
        and ref["provenance"]["file_field"] == "invoice_pdf"
        and ref["provenance"]["file_operation"] == "check"
        and ref["provenance"]["file_method"] == "request_hasfile"
        for ref in graph_refs
    )
    assert any(
        "file_field=invoice_pdf" in item["summary"]
        and "file_operation=check" in item["summary"]
        and "file_method=request_hasfile" in item["summary"]
        for item in result["items"]
    )


def test_hades_backend_graph_search_finds_local_cookie_access_edges(monkeypatch, tmp_path):
    artifact = _php_graph_artifact()
    artifact["edges"].extend(
        [
            {
                "kind": "cookie_access",
                "from": "OrderController@show",
                "to": "cookie:orders_filter",
                "cookie_name": "orders_filter",
                "cookie_operation": "set",
                "cookie_method": "cookie_queue",
                "path": "app/Http/Controllers/OrderController.php",
                "line": 50,
            },
            {
                "kind": "route_cookie_access",
                "from": "route:orders.show",
                "to": "cookie:orders_filter",
                "handler": "OrderController@show",
                "cookie_name": "orders_filter",
                "cookie_operation": "set",
                "cookie_method": "cookie_queue",
                "method": "GET",
                "uri": "/orders/{order}",
                "path": "routes/web.php",
                "line": 4,
                "source_path": "app/Http/Controllers/OrderController.php",
                "source_line": 50,
            },
        ]
    )
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
                "payload": artifact,
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
            {"query": "orders cookie orders_filter queue set", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "route_cookie_access"
        and ref["from"] == "route:orders.show"
        and ref["to"] == "cookie:orders_filter"
        and ref["provenance"]["cookie_name"] == "orders_filter"
        and ref["provenance"]["cookie_operation"] == "set"
        and ref["provenance"]["cookie_method"] == "cookie_queue"
        for ref in graph_refs
    )
    assert any(
        "cookie_name=orders_filter" in item["summary"]
        and "cookie_operation=set" in item["summary"]
        and "cookie_method=cookie_queue" in item["summary"]
        for item in result["items"]
    )


def test_hades_backend_graph_search_finds_local_db_transaction_edges(monkeypatch, tmp_path):
    artifact = _php_graph_artifact()
    artifact["edges"].extend(
        [
            {
                "kind": "db_transaction",
                "from": "OrderController@show",
                "to": "db_transaction:transaction",
                "transaction_operation": "transaction",
                "transaction_method": "DB::transaction",
                "path": "app/Http/Controllers/OrderController.php",
                "line": 51,
            },
            {
                "kind": "route_db_transaction",
                "from": "route:orders.show",
                "to": "db_transaction:transaction",
                "handler": "OrderController@show",
                "transaction_operation": "transaction",
                "transaction_method": "DB::transaction",
                "method": "GET",
                "uri": "/orders/{order}",
                "path": "routes/web.php",
                "line": 4,
                "source_path": "app/Http/Controllers/OrderController.php",
                "source_line": 51,
            },
        ]
    )
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
                "payload": artifact,
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
            {"query": "orders DB transaction rollback", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "route_db_transaction"
        and ref["from"] == "route:orders.show"
        and ref["to"] == "db_transaction:transaction"
        and ref["provenance"]["transaction_operation"] == "transaction"
        and ref["provenance"]["transaction_method"] == "DB::transaction"
        for ref in graph_refs
    )
    assert any(
        "transaction_operation=transaction" in item["summary"]
        and "transaction_method=DB::transaction" in item["summary"]
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


def test_hades_backend_graph_search_finds_local_model_trait_edges(monkeypatch, tmp_path):
    graph_payload = _php_graph_artifact()
    graph_payload["edges"].append(
        {
            "kind": "model_trait",
            "from": "App\\Models\\Order",
            "to": "Illuminate\\Database\\Eloquent\\SoftDeletes",
            "trait_class": "Illuminate\\Database\\Eloquent\\SoftDeletes",
            "trait_short_name": "SoftDeletes",
            "table": "orders",
            "path": "app/Models/Order.php",
            "line": 31,
        }
    )
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
                "payload": graph_payload,
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
            {"query": "orders model SoftDeletes trait", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "model_trait"
        and ref["from"] == "App\\Models\\Order"
        and ref["to"] == "Illuminate\\Database\\Eloquent\\SoftDeletes"
        for ref in graph_refs
    )
    assert any(
        "trait_short_name=SoftDeletes" in item["summary"]
        and "trait_class=Illuminate\\Database\\Eloquent\\SoftDeletes" in item["summary"]
        and "table=orders" in item["summary"]
        for item in result["items"]
    )


def test_hades_backend_graph_search_finds_local_observer_method_edges(monkeypatch, tmp_path):
    graph_payload = _php_graph_artifact()
    graph_payload["edges"].append(
        {
            "kind": "observed_by_method",
            "from": "App\\Models\\Order",
            "to": "OrderObserver@updated",
            "observer_class": "App\\Observers\\OrderObserver",
            "observer_method": "updated",
            "lifecycle_event": "updated",
            "table": "orders",
            "path": "app/Providers/AuthServiceProvider.php",
            "line": 18,
        }
    )
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
                "payload": graph_payload,
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
            {"query": "orders observer updated lifecycle", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "observed_by_method"
        and ref["from"] == "App\\Models\\Order"
        and ref["to"] == "OrderObserver@updated"
        for ref in graph_refs
    )
    assert any(
        "observer_class=App\\Observers\\OrderObserver" in item["summary"]
        and "observer_method=updated" in item["summary"]
        and "lifecycle_event=updated" in item["summary"]
        for item in result["items"]
    )


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
            {"query": "orders customer_id validation request", "limit": 10},
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


def test_hades_backend_graph_search_finds_local_validation_database_rule_edges(monkeypatch, tmp_path):
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
            {"query": "customer_id exists customers validation", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "route_validation_database_rule"
        and ref["to"] == "table:customers.id"
        and ref["provenance"]["rule"] == "exists"
        for ref in graph_refs
    )
    assert any(
        "field=customer_id" in item["summary"]
        and "rule=exists" in item["summary"]
        and "table=customers" in item["summary"]
        and "column=id" in item["summary"]
        for item in result["items"]
    )


def test_hades_backend_graph_search_finds_local_form_request_authorization_edges(monkeypatch, tmp_path):
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
            {"query": "StoreOrderRequest authorize deny route 403", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "route_request_authorization"
        and ref["to"] == "App\\Http\\Requests\\StoreOrderRequest"
        and ref["provenance"]["authorization_result"] == "deny"
        for ref in graph_refs
    )
    assert any(
        "authorization_result=deny" in item["summary"]
        and "authorization_path=app/Http/Requests/StoreOrderRequest.php" in item["summary"]
        for item in result["items"]
    )


def test_hades_backend_graph_search_finds_local_form_request_input_mutation_edges(monkeypatch, tmp_path):
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
            {"query": "prepareForValidation status merge request", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "route_request_input_mutation"
        and ref["to"] == "request_field:status"
        and ref["provenance"]["mutation_stage"] == "prepare_for_validation"
        for ref in graph_refs
    )
    assert any(
        "field=status" in item["summary"]
        and "operation=merge" in item["summary"]
        and "mutation_stage=prepare_for_validation" in item["summary"]
        for item in result["items"]
    )


def test_hades_backend_graph_search_finds_local_http_abort_edges(monkeypatch, tmp_path):
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
            {"query": "route abort_if 403", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "route_http_abort"
        and ref["to"] == "http_status:403"
        and ref["provenance"]["status_code"] == 403
        and ref["provenance"]["abort_helper"] == "abort_if"
        for ref in graph_refs
    )
    assert any(
        "status_code=403" in item["summary"]
        and "abort_helper=abort_if" in item["summary"]
        and "source_path=app/Http/Controllers/OrderController.php" in item["summary"]
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
            {"query": "orders authorization view policy", "limit": 10},
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
        ref["type"] == "edge"
        and ref["kind"] == "route_authorization_policy_method"
        and ref["to"] == "OrderPolicy@view"
        and ref["provenance"]["policy_class"] == "App\\Policies\\OrderPolicy"
        for ref in graph_refs
    )
    assert any(
        "ability=view" in item["summary"]
        and "target_model=App\\Models\\Order" in item["summary"]
        and "source=this_authorize" in item["summary"]
        for item in result["items"]
    )
    assert any(
        "policy_class=App\\Policies\\OrderPolicy" in item["summary"]
        and "ability=view" in item["summary"]
        for item in result["items"]
    )


def test_hades_backend_graph_search_finds_local_policy_mapping_edges(monkeypatch, tmp_path):
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
            {"query": "OrderPolicy policies_property order policy", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "policy_for"
        and ref["from"] == "App\\Models\\Order"
        and ref["to"] == "App\\Policies\\OrderPolicy"
        and ref["provenance"]["source"] == "policies_property"
        for ref in graph_refs
    )
    assert any(
        "source=policies_property" in item["summary"]
        and "app/Providers/AuthServiceProvider.php" in item["summary"]
        for item in result["items"]
    )


def test_hades_backend_graph_search_finds_local_middleware_method_edges(monkeypatch, tmp_path):
    artifact = _php_graph_artifact()
    artifact["routes"][0]["middleware"] = ["web", "auth", "verified"]
    artifact["symbols"].extend(
        [
            {
                "kind": "method",
                "name": "Authenticate@handle",
                "class": "App\\Http\\Middleware\\Authenticate",
                "method": "handle",
                "role": "middleware",
                "path": "app/Http/Middleware/Authenticate.php",
                "line": 4,
            },
            {
                "kind": "method",
                "name": "EncryptCookies@handle",
                "class": "App\\Http\\Middleware\\EncryptCookies",
                "method": "handle",
                "role": "middleware",
                "path": "app/Http/Middleware/EncryptCookies.php",
                "line": 4,
            },
            {
                "kind": "method",
                "name": "EnsureEmailIsVerified@handle",
                "class": "App\\Http\\Middleware\\EnsureEmailIsVerified",
                "method": "handle",
                "role": "middleware",
                "path": "app/Http/Middleware/EnsureEmailIsVerified.php",
                "line": 4,
            },
        ]
    )
    artifact["edges"].extend(
        [
            {
                "kind": "route_middleware_method",
                "from": "route:orders.show",
                "to": "Authenticate@handle",
                "middleware": "auth",
                "middleware_class": "App\\Http\\Middleware\\Authenticate",
                "method": "GET",
                "uri": "/orders/{order}",
                "path": "routes/web.php",
                "line": 4,
            },
            {
                "kind": "route_middleware_method",
                "from": "route:orders.show",
                "to": "EncryptCookies@handle",
                "middleware": "web",
                "middleware_class": "App\\Http\\Middleware\\EncryptCookies",
                "via": "App\\Http\\Middleware\\EncryptCookies",
                "method": "GET",
                "uri": "/orders/{order}",
                "path": "routes/web.php",
                "line": 4,
            },
            {
                "kind": "route_middleware_method",
                "from": "route:orders.show",
                "to": "EnsureEmailIsVerified@handle",
                "middleware": "verified",
                "middleware_class": "App\\Http\\Middleware\\EnsureEmailIsVerified",
                "method": "GET",
                "uri": "/orders/{order}",
                "path": "routes/web.php",
                "line": 4,
            },
        ]
    )
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
                "payload": artifact,
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
            {"query": "orders middleware Authenticate verified handle", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "route_middleware_method"
        and ref["from"] == "route:orders.show"
        and ref["to"] == "Authenticate@handle"
        and ref["provenance"]["middleware"] == "auth"
        and ref["provenance"]["middleware_class"] == "App\\Http\\Middleware\\Authenticate"
        for ref in graph_refs
    )
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "route_middleware_method"
        and ref["from"] == "route:orders.show"
        and ref["to"] == "EnsureEmailIsVerified@handle"
        and ref["provenance"]["middleware"] == "verified"
        and ref["provenance"]["middleware_class"] == "App\\Http\\Middleware\\EnsureEmailIsVerified"
        for ref in graph_refs
    )
    assert any(
        "middleware=auth" in item["summary"]
        and "middleware_class=App\\Http\\Middleware\\Authenticate" in item["summary"]
        for item in result["items"]
    )


def test_hades_backend_graph_search_finds_local_middleware_parameter_edges(monkeypatch, tmp_path):
    artifact = _php_graph_artifact()
    artifact["routes"][0]["middleware"] = ["throttle:60,1"]
    artifact["symbols"].append(
        {
            "kind": "method",
            "name": "ThrottleRequests@handle",
            "class": "App\\Http\\Middleware\\ThrottleRequests",
            "method": "handle",
            "role": "middleware",
            "path": "app/Http/Middleware/ThrottleRequests.php",
            "line": 4,
        }
    )
    artifact["edges"].append(
        {
            "kind": "route_middleware_method",
            "from": "route:orders.show",
            "to": "ThrottleRequests@handle",
            "middleware": "throttle",
            "middleware_class": "App\\Http\\Middleware\\ThrottleRequests",
            "middleware_params": ["60", "1"],
            "method": "GET",
            "uri": "/orders/{order}",
            "path": "routes/web.php",
            "line": 4,
        }
    )
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
                "payload": artifact,
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
            {"query": "orders throttle 60 1 middleware handle", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "route_middleware_method"
        and ref["from"] == "route:orders.show"
        and ref["to"] == "ThrottleRequests@handle"
        and ref["provenance"]["middleware"] == "throttle"
        and ref["provenance"]["middleware_params"] == ["60", "1"]
        for ref in graph_refs
    )
    assert any(
        "middleware=throttle" in item["summary"]
        and "middleware_params=['60', '1']" in item["summary"]
        for item in result["items"]
    )


def test_hades_backend_graph_search_finds_local_blade_route_refs(monkeypatch, tmp_path):
    graph_payload = _php_graph_artifact()
    graph_payload["edges"].append(
        {
            "kind": "blade_route_ref",
            "from": "view:orders.show",
            "to": "route:invoices.update",
            "route_name": "invoices.update",
            "path": "resources/views/orders/show.blade.php",
            "line": 6,
        }
    )
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
                "payload": graph_payload,
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
            {"query": "orders view invoices.update route", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "blade_route_ref"
        and ref["from"] == "view:orders.show"
        and ref["to"] == "route:invoices.update"
        and ref["provenance"]["route_name"] == "invoices.update"
        for ref in graph_refs
    )
    assert any(
        "route_name=invoices.update" in item["summary"]
        and "resources/views/orders/show.blade.php" in item["summary"]
        for item in result["items"]
    )


def test_hades_backend_graph_search_finds_local_blade_route_params(monkeypatch, tmp_path):
    graph_payload = _php_graph_artifact()
    graph_payload["edges"].extend(
        [
            {
                "kind": "blade_route_param",
                "from": "view:orders.show",
                "to": "route_param:invoices.update.invoice",
                "route_name": "invoices.update",
                "route_param": "invoice",
                "route_param_status": "provided",
                "route_param_required": True,
                "route_param_match": True,
                "path": "resources/views/orders/show.blade.php",
                "line": 6,
            },
            {
                "kind": "blade_route_param",
                "from": "view:orders.show",
                "to": "route_param:orders.show.order",
                "route_name": "orders.show",
                "route_param": "order",
                "route_param_status": "missing",
                "route_param_required": True,
                "route_param_match": False,
                "path": "resources/views/orders/show.blade.php",
                "line": 13,
            },
        ]
    )
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
                "payload": graph_payload,
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
            {"query": "orders view route param invoice order missing provided", "limit": 20},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "blade_route_param"
        and ref["to"] == "route_param:invoices.update.invoice"
        and ref["provenance"]["route_param_status"] == "provided"
        and ref["provenance"]["route_param_match"] is True
        for ref in graph_refs
    )
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "blade_route_param"
        and ref["to"] == "route_param:orders.show.order"
        and ref["provenance"]["route_param_status"] == "missing"
        and ref["provenance"]["route_param_match"] is False
        for ref in graph_refs
    )
    assert any(
        "route_param=order" in item["summary"]
        and "route_param_status=missing" in item["summary"]
        and "route_param_match=False" in item["summary"]
        for item in result["items"]
    )


def test_hades_backend_graph_search_finds_local_blade_authorization_edges(monkeypatch, tmp_path):
    graph_payload = _php_graph_artifact()
    graph_payload["edges"].append(
        {
            "kind": "blade_authorization",
            "from": "view:orders.show",
            "to": "ability:view",
            "ability": "view",
            "authorization_helper": "can",
            "path": "resources/views/orders/show.blade.php",
            "line": 14,
        }
    )
    provider = _create_linked_provider(
        monkeypatch,
        tmp_path,
        items=[
            {
                "id": "artifact_1",
                "domain": "artifacts",
                "schema": "hades.php_graph.v1",
                "source": "hades.php_graph.v1",
                "summary": "Laravel graph artifact for order view authorization.",
                "payload": graph_payload,
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
            {"query": "orders view blade authorization can ability view", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "blade_authorization"
        and ref["from"] == "view:orders.show"
        and ref["to"] == "ability:view"
        and ref["provenance"]["ability"] == "view"
        and ref["provenance"]["authorization_helper"] == "can"
        for ref in graph_refs
    )
    assert any(
        "ability=view" in item["summary"] and "authorization_helper=can" in item["summary"]
        for item in result["items"]
    )


def test_hades_backend_graph_search_finds_local_blade_form_field_edges(monkeypatch, tmp_path):
    graph_payload = _php_graph_artifact()
    graph_payload["edges"].extend(
        [
            {
                "kind": "blade_form_field",
                "from": "view:orders.show",
                "to": "request_field:customer_id",
                "form_field": "customer_id",
                "form_field_tag": "input",
                "path": "resources/views/orders/show.blade.php",
                "line": 17,
            },
            {
                "kind": "blade_old_input",
                "from": "view:orders.show",
                "to": "request_field:customer_id",
                "form_field": "customer_id",
                "input_helper": "old",
                "path": "resources/views/orders/show.blade.php",
                "line": 17,
            },
            {
                "kind": "blade_validation_error",
                "from": "view:orders.show",
                "to": "validation:customer_id",
                "form_field": "customer_id",
                "validation_helper": "error",
                "path": "resources/views/orders/show.blade.php",
                "line": 18,
            },
        ]
    )
    provider = _create_linked_provider(
        monkeypatch,
        tmp_path,
        items=[
            {
                "id": "artifact_1",
                "domain": "artifacts",
                "schema": "hades.php_graph.v1",
                "source": "hades.php_graph.v1",
                "summary": "Laravel graph artifact for order view form field.",
                "payload": graph_payload,
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
            {"query": "orders view customer_id input old error validation", "limit": 20},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "blade_form_field"
        and ref["to"] == "request_field:customer_id"
        and ref["provenance"]["form_field_tag"] == "input"
        for ref in graph_refs
    )
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "blade_old_input"
        and ref["to"] == "request_field:customer_id"
        and ref["provenance"]["input_helper"] == "old"
        for ref in graph_refs
    )
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "blade_validation_error"
        and ref["to"] == "validation:customer_id"
        and ref["provenance"]["validation_helper"] == "error"
        for ref in graph_refs
    )
    assert any(
        "form_field=customer_id" in item["summary"]
        and "validation_helper=error" in item["summary"]
        for item in result["items"]
    )


def test_hades_backend_graph_search_finds_local_blade_wire_model_edges(monkeypatch, tmp_path):
    graph_payload = _php_graph_artifact()
    graph_payload["edges"].append(
        {
            "kind": "blade_wire_model",
            "from": "view:orders.show",
            "to": "livewire_property:status",
            "wire_model": "status",
            "wire_modifiers": ["defer"],
            "path": "resources/views/orders/show.blade.php",
            "line": 21,
        }
    )
    provider = _create_linked_provider(
        monkeypatch,
        tmp_path,
        items=[
            {
                "id": "artifact_1",
                "domain": "artifacts",
                "schema": "hades.php_graph.v1",
                "source": "hades.php_graph.v1",
                "summary": "Laravel graph artifact for Livewire status field.",
                "payload": graph_payload,
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
            {"query": "orders view livewire wire model status defer", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "blade_wire_model"
        and ref["from"] == "view:orders.show"
        and ref["to"] == "livewire_property:status"
        and ref["provenance"]["wire_model"] == "status"
        and ref["provenance"]["wire_modifiers"] == ["defer"]
        for ref in graph_refs
    )
    assert any(
        "wire_model=status" in item["summary"] and "wire_modifiers=['defer']" in item["summary"]
        for item in result["items"]
    )


def test_hades_backend_graph_search_finds_local_blade_wire_action_edges(monkeypatch, tmp_path):
    graph_payload = _php_graph_artifact()
    graph_payload["edges"].extend(
        [
            {
                "kind": "livewire_component_class",
                "from": "livewire:orders-status",
                "to": "App\\Livewire\\OrdersStatus",
                "livewire_alias": "orders-status",
                "livewire_class": "App\\Livewire\\OrdersStatus",
                "path": "resources/views/orders/show.blade.php",
                "line": 5,
            },
            {
                "kind": "blade_wire_action",
                "from": "view:orders.show",
                "to": "livewire_action:saveOrder",
                "wire_action": "saveOrder",
                "wire_event": "click",
                "wire_modifiers": ["debounce"],
                "path": "resources/views/orders/show.blade.php",
                "line": 22,
            },
            {
                "kind": "blade_wire_action_method",
                "from": "livewire_action:saveOrder",
                "to": "OrdersStatus@saveOrder",
                "livewire_alias": "orders-status",
                "livewire_class": "App\\Livewire\\OrdersStatus",
                "wire_action": "saveOrder",
                "path": "resources/views/orders/show.blade.php",
                "line": 22,
            },
        ]
    )
    provider = _create_linked_provider(
        monkeypatch,
        tmp_path,
        items=[
            {
                "id": "artifact_1",
                "domain": "artifacts",
                "schema": "hades.php_graph.v1",
                "source": "hades.php_graph.v1",
                "summary": "Laravel graph artifact for Livewire save action.",
                "payload": graph_payload,
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
            {"query": "orders view livewire wire click saveOrder debounce action", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "blade_wire_action"
        and ref["from"] == "view:orders.show"
        and ref["to"] == "livewire_action:saveOrder"
        and ref["provenance"]["wire_action"] == "saveOrder"
        and ref["provenance"]["wire_event"] == "click"
        and ref["provenance"]["wire_modifiers"] == ["debounce"]
        for ref in graph_refs
    )
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "livewire_component_class"
        and ref["from"] == "livewire:orders-status"
        and ref["to"] == "App\\Livewire\\OrdersStatus"
        and ref["provenance"]["livewire_alias"] == "orders-status"
        for ref in graph_refs
    )
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "blade_wire_action_method"
        and ref["from"] == "livewire_action:saveOrder"
        and ref["to"] == "OrdersStatus@saveOrder"
        and ref["provenance"]["livewire_class"] == "App\\Livewire\\OrdersStatus"
        for ref in graph_refs
    )
    assert any(
        "wire_action=saveOrder" in item["summary"]
        and "wire_event=click" in item["summary"]
        and "wire_modifiers=['debounce']" in item["summary"]
        for item in result["items"]
    )
    assert any(
        "livewire_alias=orders-status" in item["summary"]
        and "livewire_class=App\\Livewire\\OrdersStatus" in item["summary"]
        for item in result["items"]
    )


def test_hades_backend_graph_search_finds_local_blade_form_metadata(monkeypatch, tmp_path):
    graph_payload = _php_graph_artifact()
    graph_payload["edges"].extend(
        [
            {
                "kind": "blade_csrf_token",
                "from": "view:orders.show",
                "to": "csrf:present",
                "csrf": "present",
                "path": "resources/views/orders/show.blade.php",
                "line": 7,
            },
            {
                "kind": "blade_form_method",
                "from": "view:orders.show",
                "to": "http_method:PUT",
                "form_method": "PUT",
                "path": "resources/views/orders/show.blade.php",
                "line": 8,
            },
        ]
    )
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
                "payload": graph_payload,
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
            {"query": "orders view csrf PUT form method", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "blade_csrf_token"
        and ref["from"] == "view:orders.show"
        and ref["to"] == "csrf:present"
        and ref["provenance"]["csrf"] == "present"
        for ref in graph_refs
    )
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "blade_form_method"
        and ref["from"] == "view:orders.show"
        and ref["to"] == "http_method:PUT"
        and ref["provenance"]["form_method"] == "PUT"
        for ref in graph_refs
    )
    assert any("csrf=present" in item["summary"] for item in result["items"])
    assert any("form_method=PUT" in item["summary"] for item in result["items"])


def test_hades_backend_graph_search_finds_local_blade_form_route_method_edges(monkeypatch, tmp_path):
    graph_payload = _php_graph_artifact()
    graph_payload["edges"].append(
        {
            "kind": "blade_form_route_method",
            "from": "view:orders.show",
            "to": "route:invoices.update",
            "route_name": "invoices.update",
            "form_method": "PUT",
            "route_method": "PUT",
            "route_method_match": True,
            "path": "resources/views/orders/show.blade.php",
            "line": 8,
        }
    )
    graph_payload["edges"].append(
        {
            "kind": "blade_form_route_method",
            "from": "view:orders.show",
            "to": "route:invoices.store",
            "route_name": "invoices.store",
            "form_method": "POST",
            "route_method": "POST",
            "route_method_match": True,
            "path": "resources/views/orders/show.blade.php",
            "line": 10,
        }
    )
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
                "payload": graph_payload,
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
            {"query": "orders view invoices POST PUT route method match", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "blade_form_route_method"
        and ref["from"] == "view:orders.show"
        and ref["to"] == "route:invoices.update"
        and ref["provenance"]["route_method_match"] is True
        for ref in graph_refs
    )
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "blade_form_route_method"
        and ref["from"] == "view:orders.show"
        and ref["to"] == "route:invoices.store"
        and ref["provenance"]["form_method"] == "POST"
        and ref["provenance"]["route_method"] == "POST"
        for ref in graph_refs
    )
    assert any(
        "route_name=invoices.update" in item["summary"]
        and "form_method=PUT" in item["summary"]
        and "route_method=PUT" in item["summary"]
        and "route_method_match=True" in item["summary"]
        for item in result["items"]
    )


def test_hades_backend_graph_search_finds_local_event_listener_edges(monkeypatch, tmp_path):
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
            {"query": "orders OrderPlaced SendOrderReceipt listener", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "route_emits_event_listener"
        and ref["from"] == "route:orders.show"
        and ref["to"] == "SendOrderReceipt@handle"
        and ref["provenance"]["event_class"] == "App\\Events\\OrderPlaced"
        and ref["provenance"]["listener_class"] == "App\\Listeners\\SendOrderReceipt"
        for ref in graph_refs
    )
    assert any(
        "event_class=App\\Events\\OrderPlaced" in item["summary"]
        and "listener_class=App\\Listeners\\SendOrderReceipt" in item["summary"]
        for item in result["items"]
    )


def test_hades_backend_graph_search_finds_local_job_dispatch_method_edges(monkeypatch, tmp_path):
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
            {"query": "orders SyncOrderJob dispatch handle", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "route_dispatches_job_method"
        and ref["from"] == "route:orders.show"
        and ref["to"] == "SyncOrderJob@handle"
        and ref["provenance"]["job_class"] == "App\\Jobs\\SyncOrderJob"
        and ref["provenance"]["dispatch_method"] == "dispatch"
        for ref in graph_refs
    )
    assert any(
        "job_class=App\\Jobs\\SyncOrderJob" in item["summary"]
        and "dispatch_method=dispatch" in item["summary"]
        for item in result["items"]
    )


def test_hades_backend_graph_search_finds_local_scheduled_handle_edges(monkeypatch, tmp_path):
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
            {"query": "schedule orders sync handle hourly daily", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "scheduled_command_method"
        and ref["to"] == "SyncOrdersCommand@handle"
        and ref["provenance"]["command"] == "command:orders:sync"
        and ref["provenance"]["cadence"] == "hourly"
        for ref in graph_refs
    )
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "scheduled_job_method"
        and ref["to"] == "SyncOrderJob@handle"
        and ref["provenance"]["job_class"] == "App\\Jobs\\SyncOrderJob"
        and ref["provenance"]["cadence"] == "daily"
        for ref in graph_refs
    )
    assert any(
        "command=command:orders:sync" in item["summary"]
        and "cadence=hourly" in item["summary"]
        for item in result["items"]
    )


def test_hades_backend_graph_search_finds_local_mail_notification_edges(monkeypatch, tmp_path):
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
            {"query": "orders receipt mail notification toMail", "limit": 10},
        )
    )

    graph_refs = [item["graph_ref"] for item in result["items"]]

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "route_sends_mail_method"
        and ref["to"] == "OrderReceiptMail@build"
        and ref["provenance"]["mailable_class"] == "App\\Mail\\OrderReceiptMail"
        and ref["provenance"]["mail_method"] == "send"
        for ref in graph_refs
    )
    assert any(
        ref["type"] == "edge"
        and ref["kind"] == "route_sends_notification_method"
        and ref["to"] == "OrderShippedNotification@toMail"
        and ref["provenance"]["notification_class"] == "App\\Notifications\\OrderShippedNotification"
        and ref["provenance"]["notification_source"] == "notifiable_notify"
        for ref in graph_refs
    )
    assert any(
        "mailable_class=App\\Mail\\OrderReceiptMail" in item["summary"]
        and "mail_method=send" in item["summary"]
        for item in result["items"]
    )


def test_hades_backend_graph_traverse_finds_local_scheduled_handle_edges(monkeypatch, tmp_path):
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
            {"start": "App\\Console\\Kernel", "direction": "out", "max_depth": 1, "limit": 20},
        )
    )

    node_ids = {node["id"] for node in result["nodes"]}
    edge_kinds = {edge["kind"] for edge in result["edges"]}

    assert result["status"] == "ok"
    assert result["searched_cache_only"] is True
    assert {"App\\Console\\Kernel", "SyncOrdersCommand@handle", "SyncOrderJob@handle"} <= node_ids
    assert {"scheduled_command_method", "scheduled_job_method"} <= edge_kinds


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
            {"start": "route:orders.show", "direction": "out", "max_depth": 3, "limit": 50},
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
        "OrderService@format",
        "SyncOrderJob@handle",
        "SendOrderReceipt@handle",
        "OrderReceiptMail@build",
        "OrderShippedNotification@toMail",
        "App\\Exceptions\\OrderLockedException",
        "view:orders.show",
    } <= node_ids
    assert {
        "route_handler",
        "calls_method",
        "throws_exception",
        "route_dispatches_job_method",
        "route_emits_event_listener",
        "route_sends_mail_method",
        "route_sends_notification_method",
        "view_ref",
    } <= edge_kinds
    assert result["provenance"]["artifacts"][0]["origin"] == "memory_cache"

    view_result = json.loads(
        provider.handle_tool_call(
            "hades_backend_graph_traverse",
            {"start": "view:orders.show", "direction": "out", "max_depth": 1, "limit": 10},
        )
    )
    view_node_ids = {node["id"] for node in view_result["nodes"]}
    view_edge_kinds = {edge["kind"] for edge in view_result["edges"]}

    assert view_result["status"] == "ok"
    assert view_result["searched_cache_only"] is True
    assert {"view:orders.show", "view:layouts.app", "component:alert"} <= view_node_ids
    assert {"blade_extends", "blade_component"} <= view_edge_kinds


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
