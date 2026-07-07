from __future__ import annotations

import pytest


def _symlink_or_skip(link, target, *, target_is_directory=False):
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable in test environment: {exc}")


def test_read_files_job_redacts_and_bounds_payload(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    (tmp_path / "safe.txt").write_text("hello\nOPENAI_API_KEY=sk-live-secret\n", encoding="utf-8")

    result = execute_job(
        {
            "job_id": "job_1",
            "capability": "read_files",
            "payload": {"paths": ["safe.txt"], "max_bytes": 200},
        },
        workspace_root=tmp_path,
    )

    assert result["status"] == "completed"
    assert result["summary"].startswith("Read 1 file")
    assert "sk-live-secret" not in str(result)
    assert result["attachments"][0]["path"] == "safe.txt"
    assert result["attachments"][0]["redactions"] >= 1


def test_read_source_slice_job_redacts_and_bounds_line_window(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    source = "\n".join(
        [
            "<?php",
            "class OrderController {",
            "    public function show($order) {",
            "        $token = 'sk-live-secret';",
            "        return $order;",
            "    }",
            "}",
        ]
    )
    (tmp_path / "app" / "Http" / "Controllers").mkdir(parents=True)
    (tmp_path / "app" / "Http" / "Controllers" / "OrderController.php").write_text(source, encoding="utf-8")

    result = execute_job(
        {
            "job_id": "job_slice",
            "capability": "read_source_slice",
            "payload": {
                "path": "app/Http/Controllers/OrderController.php",
                "start_line": 3,
                "end_line": 5,
                "symbol": "OrderController@show",
            },
        },
        workspace_root=tmp_path,
    )

    source_slice = result["source_slice"]

    assert result["status"] == "completed"
    assert source_slice["path"] == "app/Http/Controllers/OrderController.php"
    assert source_slice["start_line"] == 3
    assert source_slice["end_line"] == 5
    assert source_slice["language"] == "php"
    assert source_slice["symbol"] == "OrderController@show"
    assert source_slice["retention_class"] == "source_slice"
    assert source_slice["raw_source_included"] is True
    assert "sk-live-secret" not in source_slice["content_redacted"]
    assert source_slice["redactions"] == 1


def test_read_source_slice_job_omits_sensitive_path(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    (tmp_path / ".env").write_text("TOKEN=super-secret\n", encoding="utf-8")

    result = execute_job(
        {
            "job_id": "job_slice_secret",
            "capability": "read_source_slice",
            "payload": {"path": ".env", "start_line": 1, "end_line": 1},
        },
        workspace_root=tmp_path,
    )

    assert result["status"] == "failed"
    assert result["omitted"] == [{"path": ".env", "reason": "sensitive_name"}]
    assert "super-secret" not in str(result)


def test_sync_git_tree_returns_bounded_manifest(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    (tmp_path / ".git").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")

    result = execute_job(
        {
            "job_id": "job_2",
            "capability": "sync_git_tree",
            "payload": {"max_files": 10, "max_bytes": 20_000},
        },
        workspace_root=tmp_path,
    )

    paths = {item["path"] for item in result["artifact"]["files"]}

    assert result["status"] == "completed"
    assert result["artifact"]["schema"] == "hades.git_tree.v1"
    assert "pyproject.toml" in paths
    assert "src/app.py" in paths
    assert all(not path.startswith(".git") for path in paths)


def test_project_inspection_is_metadata_tree_alias_without_raw_source(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    (tmp_path / "README.md").write_text("secret-token-123\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "service.py").write_text("def run():\n    return 'raw source'\n", encoding="utf-8")

    result = execute_job(
        {
            "job_id": "job_project_inspection",
            "capability": "project_inspection",
            "payload": {"max_files": 10, "max_bytes": 20_000},
        },
        workspace_root=tmp_path,
    )

    artifact = result["artifact"]
    paths = {item["path"] for item in artifact["files"]}

    assert result["status"] == "completed"
    assert result["summary"] == "Collected 2 project metadata entries; raw source not included."
    assert artifact["schema"] == "hades.git_tree.v1"
    assert artifact["requested_capability"] == "project_inspection"
    assert artifact["inspection_mode"] == "metadata_tree"
    assert artifact["raw_source_included"] is False
    assert paths == {"README.md", "src/service.py"}
    assert "return 'raw source'" not in str(result)
    assert "secret-token-123" not in str(result)


def test_sync_git_tree_omits_symlink_file_and_directory_escapes(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    workspace = tmp_path / "repo"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (workspace / "README.md").write_text("safe\n", encoding="utf-8")
    (outside / "secret.txt").write_text("outside-secret\n", encoding="utf-8")
    _symlink_or_skip(workspace / "leak.txt", outside / "secret.txt")
    _symlink_or_skip(workspace / "linked-outside", outside, target_is_directory=True)

    result = execute_job(
        {
            "job_id": "job_symlink",
            "capability": "sync_git_tree",
            "payload": {"max_files": 20, "max_bytes": 100_000},
        },
        workspace_root=workspace,
    )

    artifact = result["artifact"]
    paths = {item["path"] for item in artifact["files"]}
    omitted = {item["path"]: item["reason"] for item in artifact["omitted"]}

    assert "README.md" in paths
    assert "leak.txt" not in paths
    assert all(not path.startswith("linked-outside/") for path in paths)
    assert omitted["leak.txt"] == "symlink"
    assert omitted["linked-outside"] == "symlink"
    assert "outside-secret" not in str(result)


def test_sync_git_tree_prunes_gitignored_directories(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".gitignore").write_text("ignored_dir/\n", encoding="utf-8")
    (workspace / "README.md").write_text("safe\n", encoding="utf-8")
    ignored = workspace / "ignored_dir"
    ignored.mkdir()
    (ignored / "secret.txt").write_text("ignored-secret\n", encoding="utf-8")

    result = execute_job(
        {
            "job_id": "job_ignored_dir",
            "capability": "sync_git_tree",
            "payload": {"max_files": 20, "max_bytes": 100_000},
        },
        workspace_root=workspace,
    )

    artifact = result["artifact"]
    paths = {item["path"] for item in artifact["files"]}
    omitted = {item["path"]: item["reason"] for item in artifact["omitted"]}

    assert "README.md" in paths
    assert "ignored_dir/secret.txt" not in paths
    assert omitted["ignored_dir"] == "gitignored"
    assert "ignored-secret" not in str(result)


def test_sync_git_tree_respects_file_budget(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    for idx in range(5):
        (tmp_path / f"file_{idx}.txt").write_text(f"{idx}\n", encoding="utf-8")

    result = execute_job(
        {
            "job_id": "job_budget",
            "capability": "sync_git_tree",
            "payload": {"max_files": 2, "max_bytes": 100_000},
        },
        workspace_root=tmp_path,
    )

    assert result["artifact"]["truncated"] is True
    assert len(result["artifact"]["files"]) == 2


def test_sync_git_tree_omits_per_file_hash_errors(monkeypatch, tmp_path):
    import hermes_cli.hades_backend_jobs as jobs

    good = tmp_path / "good.txt"
    bad = tmp_path / "bad.txt"
    good.write_text("ok\n", encoding="utf-8")
    bad.write_text("nope\n", encoding="utf-8")
    original_hash_file = jobs._hash_file

    def fake_hash_file(path):
        if path.name == "bad.txt":
            raise OSError(13, "permission denied")
        return original_hash_file(path)

    monkeypatch.setattr(jobs, "_hash_file", fake_hash_file)

    result = jobs.execute_job(
        {
            "job_id": "job_hash_error",
            "capability": "sync_git_tree",
            "payload": {"max_files": 10, "max_bytes": 100_000},
        },
        workspace_root=tmp_path,
    )

    paths = {item["path"] for item in result["artifact"]["files"]}
    omitted = {item["path"]: item["reason"] for item in result["artifact"]["omitted"]}

    assert result["status"] == "completed"
    assert "good.txt" in paths
    assert "bad.txt" not in paths
    assert omitted["bad.txt"] == "read_error:13"


def test_populate_backend_ast_extracts_python_symbols_without_source(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "service.py").write_text(
        "class Service:\n    def run(self):\n        return 1\n\ndef helper():\n    return 2\n",
        encoding="utf-8",
    )

    result = execute_job(
        {
            "job_id": "job_3",
            "capability": "populate_backend_ast",
            "payload": {"max_files": 10, "max_symbols": 10},
        },
        workspace_root=tmp_path,
    )

    symbols = {(item["kind"], item["name"]) for item in result["artifact"]["symbols"]}

    assert result["status"] == "completed"
    assert result["artifact"]["schema"] == "hades.symbols.v1"
    assert ("class", "Service") in symbols
    assert ("function", "helper") in symbols
    assert "return 1" not in str(result)


def test_populate_backend_ast_extracts_python_web_graph_without_source(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    (tmp_path / "app").mkdir()
    (tmp_path / "project").mkdir()
    (tmp_path / "app" / "api.py").write_text(
        "import logging\n"
        "from fastapi import APIRouter, FastAPI\n"
        "from app.services import OrderService\n"
        "logger = logging.getLogger(__name__)\n"
        "app = FastAPI()\n"
        "router = APIRouter(prefix='/api')\n"
        "@router.get('/orders/{order_id}', name='orders-show')\n"
        "async def show_order(order_id: int):\n"
        "    service = OrderService()\n"
        "    logger.warning('order lookup failed')\n"
        "    return service.load(order_id)\n"
        "@app.post('/health')\n"
        "def health():\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    (tmp_path / "app" / "services.py").write_text(
        "class OrderService:\n"
        "    def load(self, order_id):\n"
        "        return {'id': order_id}\n",
        encoding="utf-8",
    )
    (tmp_path / "project" / "urls.py").write_text(
        "from django.urls import path\n"
        "from app import views\n"
        "urlpatterns = [\n"
        "    path('orders/<int:pk>/', views.order_detail, name='orders-detail'),\n"
        "    path('orders/create/', views.OrderCreateView.as_view(), name='orders-create'),\n"
        "]\n",
        encoding="utf-8",
    )
    (tmp_path / "app" / "views.py").write_text(
        "def order_detail(request, pk):\n"
        "    return None\n"
        "class OrderCreateView:\n"
        "    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "app" / "models.py").write_text(
        "from django.db import models\n"
        "class Customer(models.Model):\n"
        "    email = models.EmailField(unique=True)\n"
        "    class Meta:\n"
        "        db_table = 'customers'\n"
        "class Order(models.Model):\n"
        "    customer = models.ForeignKey('Customer', on_delete=models.CASCADE)\n"
        "    status = models.CharField(max_length=32, db_index=True)\n"
        "    total = models.DecimalField(max_digits=10, decimal_places=2, null=True)\n"
        "    class Meta:\n"
        "        db_table = 'orders'\n",
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_show_order.py").write_text(
        "from app.api import show_order\n"
        "def test_show_order(client):\n"
        "    client.get('/api/orders/{order_id}')\n",
        encoding="utf-8",
    )

    result = execute_job(
        {
            "job_id": "job_py_web_graph",
            "capability": "populate_backend_ast",
            "payload": {"max_files": 10, "max_symbols": 20, "max_edges": 30},
        },
        workspace_root=tmp_path,
    )

    artifact = result["artifact"]
    routes = {(item["framework"], item["method"], item["path"], item["handler"]) for item in artifact["routes"]}
    edges = {(item["kind"], item["from"], item["to"]) for item in artifact["edges"]}
    symbols = {(item["kind"], item["name"], item["path"]) for item in artifact["symbols"]}
    tables = {item["table"]: item for item in artifact["database"]["tables"]}
    test_files = {item["path"]: item for item in artifact["tests"]["files"]}
    log_events = {item["context"]: item for item in artifact["logs"]["events"]}

    assert result["status"] == "completed"
    assert artifact["schema"] == "hades.code_graph.v1"
    assert artifact["language"] == "python"
    assert artifact["framework"] == "python_web"
    assert artifact["raw_source_included"] is False
    assert ("fastapi", "GET", "/api/orders/{order_id}", "show_order") in routes
    assert ("fastapi", "POST", "/health", "health") in routes
    assert ("django", "ROUTE", "orders/<int:pk>/", "views.order_detail") in routes
    assert ("django", "ROUTE", "orders/create/", "views.OrderCreateView.as_view") in routes
    assert ("function", "show_order", "app/api.py") in symbols
    assert ("function", "order_detail", "app/views.py") in symbols
    assert ("class", "OrderCreateView", "app/views.py") in symbols
    assert ("class", "OrderService", "app/services.py") in symbols
    assert ("class", "Customer", "app/models.py") in symbols
    assert ("class", "Order", "app/models.py") in symbols
    assert ("imports", "app/api.py", "app.services.OrderService") in edges
    assert ("route_handler", "route:orders-show", "show_order") in edges
    assert ("route_handler", "route:orders-detail", "views.order_detail") in edges
    assert ("route_handler", "route:orders-create", "views.OrderCreateView.as_view") in edges
    assert ("calls", "show_order", "app.services.OrderService") in edges
    assert ("calls", "show_order", "service.load") in edges
    assert ("calls", "show_order", "logger.warning") in edges
    assert any(edge[0] == "emits_log" and edge[1] == "show_order" for edge in edges)
    assert ("model_table", "Customer", "table:customers") in edges
    assert ("model_table", "Order", "table:orders") in edges
    assert ("foreign_key", "table:orders.customer_id", "table:customers") in edges
    assert artifact["tests"]["file_count"] == 1
    assert test_files["tests/test_show_order.py"]["test_count"] == 1
    assert test_files["tests/test_show_order.py"]["symbol_refs"] == ["show_order"]
    assert test_files["tests/test_show_order.py"]["route_refs"] == ["route:orders-show"]
    assert ("test_covers_symbol", "test:tests/test_show_order.py", "show_order") in edges
    assert ("test_covers_route", "test:tests/test_show_order.py", "route:orders-show") in edges
    assert ("test_imports", "test:tests/test_show_order.py", "app.api") in edges
    assert artifact["logs"]["schema"] == "hades.log_map.v1"
    assert artifact["logs"]["event_count"] == 1
    assert log_events["show_order"]["level"] == "warning"
    assert log_events["show_order"]["logger"] == "logger"
    assert len(log_events["show_order"]["message_sha256"]) == 64
    assert set(tables) >= {"customers", "orders"}
    assert {column["name"] for column in tables["orders"]["columns"]} >= {"customer_id", "status", "total"}
    assert tables["orders"]["foreign_keys"] == [
        {
            "table": "orders",
            "column": "customer_id",
            "references_table": "customers",
            "path": "app/models.py",
            "line": 7,
        }
    ]
    assert "return service.load" not in str(artifact)
    assert "return {'id': order_id}" not in str(artifact)
    assert "order lookup failed" not in str(artifact)
    assert "urlpatterns" not in str(artifact)
    assert "models.ForeignKey" not in str(artifact)
    assert "client.get" not in str(artifact)


def test_populate_backend_ast_extracts_django_models_graph_without_routes(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    (tmp_path / "billing").mkdir()
    (tmp_path / "billing" / "models.py").write_text(
        "from django.db import models\n"
        "class Invoice(models.Model):\n"
        "    number = models.CharField(max_length=30, unique=True)\n"
        "    paid = models.BooleanField(default=False, db_index=True)\n",
        encoding="utf-8",
    )

    result = execute_job(
        {
            "job_id": "job_django_models",
            "capability": "populate_backend_ast",
            "payload": {"max_files": 10, "max_symbols": 10, "max_edges": 10},
        },
        workspace_root=tmp_path,
    )

    artifact = result["artifact"]
    tables = {item["table"]: item for item in artifact["database"]["tables"]}
    edges = {(item["kind"], item["from"], item["to"]) for item in artifact["edges"]}

    assert result["status"] == "completed"
    assert artifact["schema"] == "hades.code_graph.v1"
    assert artifact["framework"] == "django"
    assert artifact["routes"] == []
    assert "billing_invoice" in tables
    assert {column["name"] for column in tables["billing_invoice"]["columns"]} == {"number", "paid"}
    assert ("model_table", "Invoice", "table:billing_invoice") in edges
    assert "models.CharField" not in str(artifact)


def test_populate_backend_ast_extracts_sqlalchemy_schema_graph_without_routes(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "models.py").write_text(
        "from sqlalchemy import Column, ForeignKey, Integer, Numeric, String\n"
        "from sqlalchemy.orm import declarative_base, mapped_column\n"
        "Base = declarative_base()\n"
        "class Customer(Base):\n"
        "    __tablename__ = 'customers'\n"
        "    id = Column(Integer, primary_key=True)\n"
        "    email = Column(String(120), unique=True, index=True)\n"
        "class Order(Base):\n"
        "    __tablename__ = 'orders'\n"
        "    id = Column(Integer, primary_key=True)\n"
        "    customer_id = Column(Integer, ForeignKey('customers.id'), nullable=False)\n"
        "    total = mapped_column(Numeric(10, 2), nullable=True)\n",
        encoding="utf-8",
    )

    result = execute_job(
        {
            "job_id": "job_sqlalchemy_models",
            "capability": "populate_backend_ast",
            "payload": {"max_files": 10, "max_symbols": 10, "max_edges": 10},
        },
        workspace_root=tmp_path,
    )

    artifact = result["artifact"]
    tables = {item["table"]: item for item in artifact["database"]["tables"]}
    edges = {(item["kind"], item["from"], item["to"]) for item in artifact["edges"]}

    assert result["status"] == "completed"
    assert artifact["schema"] == "hades.code_graph.v1"
    assert artifact["framework"] == "sqlalchemy"
    assert artifact["routes"] == []
    assert set(tables) >= {"customers", "orders"}
    assert {column["name"] for column in tables["orders"]["columns"]} >= {"id", "customer_id", "total"}
    assert tables["orders"]["foreign_keys"] == [
        {
            "column": "customer_id",
            "references_table": "customers",
            "references_column": "id",
            "path": "app/models.py",
            "line": 11,
            "table": "orders",
        }
    ]
    assert ("model_table", "Order", "table:orders") in edges
    assert ("foreign_key", "table:orders.customer_id", "table:customers") in edges
    assert "Column(" not in str(artifact)
    assert "ForeignKey(" not in str(artifact)


def test_populate_backend_ast_extracts_laravel_php_graph_without_source(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "artisan").write_text("#!/usr/bin/env php\n", encoding="utf-8")
    (workspace / "routes").mkdir()
    (workspace / "app" / "Http" / "Controllers").mkdir(parents=True)
    (workspace / "app" / "Http" / "Middleware").mkdir(parents=True)
    (workspace / "app" / "Http" / "Requests").mkdir(parents=True)
    (workspace / "app" / "Jobs").mkdir(parents=True)
    (workspace / "app" / "Events").mkdir(parents=True)
    (workspace / "app" / "Listeners").mkdir(parents=True)
    (workspace / "app" / "Console" / "Commands").mkdir(parents=True)
    (workspace / "app" / "Models").mkdir(parents=True)
    (workspace / "app" / "Policies").mkdir(parents=True)
    (workspace / "app" / "Providers").mkdir(parents=True)
    (workspace / "app" / "Services").mkdir(parents=True)
    (workspace / "app" / "Contracts").mkdir(parents=True)
    (workspace / "app" / "Observers").mkdir(parents=True)
    (workspace / "app" / "Broadcasting").mkdir(parents=True)
    (workspace / "database" / "migrations").mkdir(parents=True)
    (workspace / "resources" / "views" / "orders" / "partials").mkdir(parents=True)
    (workspace / "resources" / "views" / "layouts").mkdir(parents=True)
    (workspace / "resources" / "views" / "shared").mkdir(parents=True)
    (workspace / "resources" / "views" / "components" / "orders").mkdir(parents=True)
    (workspace / "routes" / "web.php").write_text(
        "<?php\n"
        "use App\\Http\\Controllers\\OrderController;\n"
        "Route::get('/orders/{order}', [OrderController::class, 'show'])"
        "->middleware(['web', 'auth', 'verified'])->name('orders.show');\n",
        encoding="utf-8",
    )
    (workspace / "app" / "Http" / "Kernel.php").write_text(
        "<?php\n"
        "namespace App\\Http;\n"
        "use App\\Http\\Middleware\\Authenticate;\n"
        "use App\\Http\\Middleware\\EncryptCookies;\n"
        "use App\\Http\\Middleware\\EnsureEmailIsVerified;\n"
        "class Kernel {\n"
        "    protected $middlewareAliases = [\n"
        "        'auth' => Authenticate::class,\n"
        "        'verified' => EnsureEmailIsVerified::class,\n"
        "    ];\n"
        "    protected $middlewareGroups = [\n"
        "        'web' => [EncryptCookies::class, 'auth'],\n"
        "    ];\n"
        "}\n",
        encoding="utf-8",
    )
    (workspace / "app" / "Http" / "Middleware" / "Authenticate.php").write_text(
        "<?php\n"
        "namespace App\\Http\\Middleware;\n"
        "class Authenticate {}\n",
        encoding="utf-8",
    )
    (workspace / "app" / "Http" / "Middleware" / "EncryptCookies.php").write_text(
        "<?php\n"
        "namespace App\\Http\\Middleware;\n"
        "class EncryptCookies {}\n",
        encoding="utf-8",
    )
    (workspace / "app" / "Http" / "Middleware" / "EnsureEmailIsVerified.php").write_text(
        "<?php\n"
        "namespace App\\Http\\Middleware;\n"
        "class EnsureEmailIsVerified {}\n",
        encoding="utf-8",
    )
    (workspace / "routes" / "channels.php").write_text(
        "<?php\n"
        "use App\\Broadcasting\\OrderChannel;\n"
        "use Illuminate\\Support\\Facades\\Broadcast;\n"
        "Broadcast::channel('orders.{order}', OrderChannel::class);\n",
        encoding="utf-8",
    )
    (workspace / "app" / "Http" / "Controllers" / "OrderController.php").write_text(
        "<?php\n"
        "namespace App\\Http\\Controllers;\n"
        "use App\\Events\\OrderPlaced;\n"
        "use App\\Http\\Requests\\StoreOrderRequest;\n"
        "use App\\Jobs\\SyncOrderJob;\n"
        "use App\\Models\\Order;\n"
        "use App\\Services\\OrderService;\n"
        "use Illuminate\\Support\\Facades\\DB;\n"
        "use Illuminate\\Support\\Facades\\Log;\n"
        "class OrderController extends Controller {\n"
        "    public function __construct(OrderService $orders) {}\n"
        "    public function show(StoreOrderRequest $request, Order $order) {\n"
        "        $request->validate(['status' => 'required|string']);\n"
        "        config('services.orders.cache');\n"
        "        env('ORDER_DEBUG');\n"
        "        Log::warning('order payment gateway degraded');\n"
        "        SyncOrderJob::dispatch($order->id);\n"
        "        event(new OrderPlaced($order));\n"
        "        DB::table('orders')->join('customers', 'orders.customer_id', '=', 'customers.id')->first();\n"
        "        DB::table('orders')->where('status', 'pending')->update(['status' => 'paid']);\n"
        "        Order::where('status', 'paid')->first();\n"
        "        OrderService::format($order);\n"
        "        return view('orders.show', ['order' => $order]);\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    (workspace / "app" / "Http" / "Requests" / "StoreOrderRequest.php").write_text(
        "<?php\n"
        "namespace App\\Http\\Requests;\n"
        "use Illuminate\\Foundation\\Http\\FormRequest;\n"
        "class StoreOrderRequest extends FormRequest {\n"
        "    public function rules(): array {\n"
        "        return ['customer_id' => 'required|integer', 'status' => 'required|string'];\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    (workspace / "app" / "Jobs" / "SyncOrderJob.php").write_text(
        "<?php\n"
        "namespace App\\Jobs;\n"
        "class SyncOrderJob {\n"
        "    public function handle() {}\n"
        "}\n",
        encoding="utf-8",
    )
    (workspace / "app" / "Events" / "OrderPlaced.php").write_text(
        "<?php\n"
        "namespace App\\Events;\n"
        "class OrderPlaced {}\n",
        encoding="utf-8",
    )
    (workspace / "app" / "Listeners" / "SendOrderReceipt.php").write_text(
        "<?php\n"
        "namespace App\\Listeners;\n"
        "class SendOrderReceipt {\n"
        "    public function handle($event) {}\n"
        "}\n",
        encoding="utf-8",
    )
    (workspace / "app" / "Console" / "Commands" / "SyncOrdersCommand.php").write_text(
        "<?php\n"
        "namespace App\\Console\\Commands;\n"
        "use Illuminate\\Console\\Command;\n"
        "class SyncOrdersCommand extends Command {\n"
        "    protected $signature = 'orders:sync {order?}';\n"
        "    public function handle() {}\n"
        "}\n",
        encoding="utf-8",
    )
    (workspace / "app" / "Console" / "Kernel.php").write_text(
        "<?php\n"
        "namespace App\\Console;\n"
        "use App\\Jobs\\SyncOrderJob;\n"
        "class Kernel {\n"
        "    protected function schedule($schedule) {\n"
        "        $schedule->command('orders:sync')->hourly();\n"
        "        $schedule->job(new SyncOrderJob())->daily();\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    (workspace / "app" / "Models" / "Order.php").write_text(
        "<?php\n"
        "namespace App\\Models;\n"
        "use Illuminate\\Database\\Eloquent\\Model;\n"
        "class Order extends Model {\n"
        "    protected $table = 'orders';\n"
        "    public function customer() {\n"
        "        return $this->belongsTo(Customer::class);\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    (workspace / "app" / "Services" / "OrderService.php").write_text(
        "<?php\n"
        "namespace App\\Services;\n"
        "class OrderService {\n"
        "    public static function format($order) { return ['id' => $order->id]; }\n"
        "}\n",
        encoding="utf-8",
    )
    (workspace / "app" / "Contracts" / "OrderFormatter.php").write_text(
        "<?php\n"
        "namespace App\\Contracts;\n"
        "interface OrderFormatter {}\n",
        encoding="utf-8",
    )
    (workspace / "app" / "Observers" / "OrderObserver.php").write_text(
        "<?php\n"
        "namespace App\\Observers;\n"
        "class OrderObserver {\n"
        "    public function updated($order) {}\n"
        "}\n",
        encoding="utf-8",
    )
    (workspace / "app" / "Broadcasting" / "OrderChannel.php").write_text(
        "<?php\n"
        "namespace App\\Broadcasting;\n"
        "class OrderChannel {}\n",
        encoding="utf-8",
    )
    (workspace / "app" / "Policies" / "OrderPolicy.php").write_text(
        "<?php\n"
        "namespace App\\Policies;\n"
        "class OrderPolicy {\n"
        "    public function view($user, $order) { return true; }\n"
        "}\n",
        encoding="utf-8",
    )
    (workspace / "app" / "Providers" / "AuthServiceProvider.php").write_text(
        "<?php\n"
        "namespace App\\Providers;\n"
        "use App\\Contracts\\OrderFormatter;\n"
        "use App\\Events\\OrderPlaced;\n"
        "use App\\Listeners\\SendOrderReceipt;\n"
        "use App\\Models\\Order;\n"
        "use App\\Observers\\OrderObserver;\n"
        "use App\\Services\\OrderService;\n"
        "use Illuminate\\Support\\Facades\\Gate;\n"
        "class AuthServiceProvider {\n"
        "    protected $listen = [OrderPlaced::class => [SendOrderReceipt::class]];\n"
        "    public function register() {\n"
        "        $this->app->singleton(OrderFormatter::class, OrderService::class);\n"
        "    }\n"
        "    public function boot() {\n"
        "        Order::observe(OrderObserver::class);\n"
        "        Gate::policy(\\App\\Models\\Order::class, \\App\\Policies\\OrderPolicy::class);\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    (workspace / "database" / "migrations" / "2026_01_01_000000_create_orders_table.php").write_text(
        "<?php\n"
        "use Illuminate\\Database\\Schema\\Blueprint;\n"
        "use Illuminate\\Support\\Facades\\Schema;\n"
        "return new class {\n"
        "    public function up() {\n"
        "        Schema::create('orders', function (Blueprint $table) {\n"
        "            $table->id();\n"
        "            $table->foreignId('customer_id')->constrained();\n"
        "            $table->string('status')->index();\n"
        "            $table->timestamps();\n"
        "        });\n"
        "    }\n"
        "};\n",
        encoding="utf-8",
    )
    (workspace / "resources" / "views" / "orders" / "show.blade.php").write_text(
        "@extends('layouts.app')\n"
        "@section('content')\n"
        "@include('orders.partials.summary')\n"
        "<x-alert type=\"info\" />\n"
        "@livewire('orders-status')\n"
        "@endsection\n",
        encoding="utf-8",
    )
    (workspace / "resources" / "views" / "orders" / "partials" / "summary.blade.php").write_text(
        "<x-orders.card />\n",
        encoding="utf-8",
    )
    (workspace / "resources" / "views" / "layouts" / "app.blade.php").write_text(
        "@includeIf('shared.flash')\n"
        "@includeWhen($showBanner, 'shared.banner')\n"
        "@yield('content')\n",
        encoding="utf-8",
    )
    (workspace / "resources" / "views" / "shared" / "flash.blade.php").write_text(
        "<div>{{ session('status') }}</div>\n",
        encoding="utf-8",
    )
    (workspace / "resources" / "views" / "shared" / "banner.blade.php").write_text(
        "<div>{{ $headline }}</div>\n",
        encoding="utf-8",
    )
    (workspace / "resources" / "views" / "components" / "alert.blade.php").write_text(
        "<div>{{ $slot }}</div>\n",
        encoding="utf-8",
    )
    (workspace / "resources" / "views" / "components" / "orders" / "card.blade.php").write_text(
        "<article>{{ $slot }}</article>\n",
        encoding="utf-8",
    )

    result = execute_job(
        {
            "job_id": "job_php_graph",
            "capability": "populate_backend_ast",
            "payload": {"max_files": 60, "max_symbols": 70, "max_edges": 180},
        },
        workspace_root=workspace,
    )

    artifact = result["artifact"]
    routes = {(item["method"], item["uri"], item["handler"]) for item in artifact["routes"]}
    symbols = {(item["kind"], item["name"]) for item in artifact["symbols"]}
    edges = {(item["kind"], item["from"], item["to"]) for item in artifact["edges"]}
    tables = {item["table"]: item for item in artifact["database"]["tables"]}
    log_events = {item["context"]: item for item in artifact["logs"]["events"]}

    assert result["status"] == "completed"
    assert artifact["schema"] == "hades.php_graph.v1"
    assert artifact["framework"] == "laravel"
    assert artifact["raw_source_included"] is False
    assert artifact["middleware"]["alias_count"] == 2
    assert artifact["middleware"]["group_count"] == 1
    assert ("GET", "/orders/{order}", "OrderController@show") in routes
    assert ("class", "App\\Http\\Controllers\\OrderController") in symbols
    assert ("class", "App\\Models\\Order") in symbols
    assert ("class", "App\\Policies\\OrderPolicy") in symbols
    assert ("class", "App\\Services\\OrderService") in symbols
    assert ("class", "App\\Http\\Requests\\StoreOrderRequest") in symbols
    assert ("class", "App\\Http\\Middleware\\Authenticate") in symbols
    assert ("class", "App\\Http\\Middleware\\EncryptCookies") in symbols
    assert ("class", "App\\Http\\Middleware\\EnsureEmailIsVerified") in symbols
    assert ("middleware_alias", "middleware:auth") in symbols
    assert ("middleware_alias", "middleware:verified") in symbols
    assert ("middleware_group", "middleware_group:web") in symbols
    assert ("class", "App\\Jobs\\SyncOrderJob") in symbols
    assert ("class", "App\\Events\\OrderPlaced") in symbols
    assert ("class", "App\\Listeners\\SendOrderReceipt") in symbols
    assert ("class", "App\\Console\\Commands\\SyncOrdersCommand") in symbols
    assert ("interface", "App\\Contracts\\OrderFormatter") in symbols
    assert ("class", "App\\Observers\\OrderObserver") in symbols
    assert ("class", "App\\Broadcasting\\OrderChannel") in symbols
    assert ("blade_view", "view:orders.show") in symbols
    assert ("blade_view", "view:orders.partials.summary") in symbols
    assert ("blade_view", "view:layouts.app") in symbols
    assert ("blade_component", "component:alert") in symbols
    assert ("blade_component", "component:orders.card") in symbols
    assert ("table", "table:orders") in symbols
    assert ("method", "OrderController@__construct") in symbols
    assert ("method", "OrderController@show") in symbols
    assert ("method", "Order@customer") in symbols
    assert ("route_handler", "route:orders.show", "OrderController@show") in edges
    assert ("route_middleware", "route:orders.show", "middleware:web") in edges
    assert ("route_middleware", "route:orders.show", "middleware:auth") in edges
    assert ("route_middleware", "route:orders.show", "middleware:verified") in edges
    assert ("route_middleware_group", "route:orders.show", "middleware_group:web") in edges
    assert ("route_middleware_class", "route:orders.show", "App\\Http\\Middleware\\Authenticate") in edges
    assert ("route_middleware_class", "route:orders.show", "App\\Http\\Middleware\\EncryptCookies") in edges
    assert ("route_middleware_class", "route:orders.show", "App\\Http\\Middleware\\EnsureEmailIsVerified") in edges
    assert ("middleware_alias_class", "middleware:auth", "App\\Http\\Middleware\\Authenticate") in edges
    assert ("middleware_alias_class", "middleware:verified", "App\\Http\\Middleware\\EnsureEmailIsVerified") in edges
    assert ("middleware_group_member", "middleware_group:web", "App\\Http\\Middleware\\EncryptCookies") in edges
    assert ("middleware_group_member", "middleware_group:web", "App\\Http\\Middleware\\Authenticate") in edges
    assert ("eloquent_relation", "App\\Models\\Order", "App\\Models\\Customer") in edges
    assert ("static_call", "App\\Http\\Controllers\\OrderController", "App\\Services\\OrderService::format") in edges
    assert ("static_call", "OrderController@show", "App\\Services\\OrderService::format") in edges
    assert ("uses_form_request", "OrderController@show", "App\\Http\\Requests\\StoreOrderRequest") in edges
    assert ("uses_dependency", "OrderController@__construct", "App\\Services\\OrderService") in edges
    assert ("uses_dependency", "OrderController@show", "App\\Models\\Order") in edges
    assert ("request_validation", "App\\Http\\Requests\\StoreOrderRequest", "validation:customer_id") in edges
    assert ("request_validation", "App\\Http\\Controllers\\OrderController", "validation:status") in edges
    assert ("request_validation", "OrderController@show", "validation:status") in edges
    assert ("dispatches_job", "App\\Http\\Controllers\\OrderController", "App\\Jobs\\SyncOrderJob") in edges
    assert ("dispatches_job", "OrderController@show", "App\\Jobs\\SyncOrderJob") in edges
    assert ("emits_event", "App\\Http\\Controllers\\OrderController", "App\\Events\\OrderPlaced") in edges
    assert ("emits_event", "OrderController@show", "App\\Events\\OrderPlaced") in edges
    assert ("event_listener", "App\\Events\\OrderPlaced", "App\\Listeners\\SendOrderReceipt") in edges
    assert ("artisan_command", "App\\Console\\Commands\\SyncOrdersCommand", "command:orders:sync") in edges
    assert ("scheduled_command", "App\\Console\\Kernel", "command:orders:sync") in edges
    assert ("scheduled_job", "App\\Console\\Kernel", "App\\Jobs\\SyncOrderJob") in edges
    assert ("query_table", "App\\Http\\Controllers\\OrderController", "table:orders") in edges
    assert ("query_table", "App\\Http\\Controllers\\OrderController", "table:customers") in edges
    assert ("query_table", "OrderController@show", "table:orders") in edges
    assert ("query_table", "OrderController@show", "table:customers") in edges
    assert ("query_operation", "OrderController@show", "query:customers:join") in edges
    assert ("query_operation", "OrderController@show", "query:orders:first") in edges
    assert ("query_operation", "OrderController@show", "query:orders:update") in edges
    assert ("query_read", "OrderController@show", "table:orders") in edges
    assert ("query_write", "OrderController@show", "table:orders") in edges
    assert {
        "kind": "query_operation",
        "from": "OrderController@show",
        "to": "query:orders:update",
        "class_context": "App\\Http\\Controllers\\OrderController",
        "table": "orders",
        "operation": "update",
        "access": "write",
        "path": "app/Http/Controllers/OrderController.php",
        "line": 20,
    } in artifact["edges"]
    assert ("eloquent_query", "App\\Http\\Controllers\\OrderController", "App\\Models\\Order::where") in edges
    assert ("eloquent_query", "OrderController@show", "App\\Models\\Order::where") in edges
    assert ("view_ref", "App\\Http\\Controllers\\OrderController", "view:orders.show") in edges
    assert ("view_ref", "OrderController@show", "view:orders.show") in edges
    assert ("blade_extends", "view:orders.show", "view:layouts.app") in edges
    assert ("blade_include", "view:orders.show", "view:orders.partials.summary") in edges
    assert ("blade_include", "view:layouts.app", "view:shared.flash") in edges
    assert ("blade_include", "view:layouts.app", "view:shared.banner") in edges
    assert ("blade_component", "view:orders.show", "component:alert") in edges
    assert ("blade_component", "view:orders.partials.summary", "component:orders.card") in edges
    assert ("livewire_component", "view:orders.show", "livewire:orders-status") in edges
    assert ("model_table", "App\\Models\\Order", "table:orders") in edges
    assert ("policy_for", "App\\Models\\Order", "App\\Policies\\OrderPolicy") in edges
    assert ("container_binding", "App\\Contracts\\OrderFormatter", "App\\Services\\OrderService") in edges
    assert ("observed_by", "App\\Models\\Order", "App\\Observers\\OrderObserver") in edges
    assert ("broadcast_channel", "routes/channels.php", "broadcast:orders.{order}") in edges
    assert ("config_ref", "App\\Http\\Controllers\\OrderController", "config:services.orders.cache") in edges
    assert ("env_ref", "App\\Http\\Controllers\\OrderController", "env:ORDER_DEBUG") in edges
    assert ("config_ref", "OrderController@show", "config:services.orders.cache") in edges
    assert ("env_ref", "OrderController@show", "env:ORDER_DEBUG") in edges
    assert ("emits_log", "OrderController@show", log_events["OrderController@show"]["id"]) in edges
    assert (
        "migration_table",
        "database/migrations/2026_01_01_000000_create_orders_table.php",
        "table:orders",
    ) in edges
    assert ("foreign_key", "table:orders.customer_id", "table:customers") in edges
    assert set(tables["orders"]) >= {"columns", "foreign_keys", "indexes"}
    assert {column["name"] for column in tables["orders"]["columns"]} >= {"id", "customer_id", "status"}
    assert tables["orders"]["foreign_keys"] == [
        {
            "table": "orders",
            "column": "customer_id",
            "references_table": "customers",
            "path": "database/migrations/2026_01_01_000000_create_orders_table.php",
            "line": 8,
        }
    ]
    broadcast_edges = [edge for edge in artifact["edges"] if edge["kind"] == "broadcast_channel"]
    assert broadcast_edges == [
        {
            "kind": "broadcast_channel",
            "from": "routes/channels.php",
            "to": "broadcast:orders.{order}",
            "path": "routes/channels.php",
            "line": 4,
            "handler": "App\\Broadcasting\\OrderChannel",
        }
    ]
    assert artifact["logs"]["schema"] == "hades.log_map.v1"
    assert artifact["logs"]["event_count"] == 1
    assert log_events["OrderController@show"]["level"] == "warning"
    assert log_events["OrderController@show"]["logger"] == "Log"
    assert len(log_events["OrderController@show"]["message_sha256"]) == 64
    assert "Route::get" not in str(artifact)
    assert "return $this->belongsTo" not in str(artifact)
    assert "return OrderService::format" not in str(artifact)
    assert "return view('orders.show'" not in str(artifact)
    assert "$this->app->singleton" not in str(artifact)
    assert "Order::observe" not in str(artifact)
    assert "Broadcast::channel" not in str(artifact)
    assert "Schema::create" not in str(artifact)
    assert "config('services.orders.cache')" not in str(artifact)
    assert "order payment gateway degraded" not in str(artifact)
    assert "pending" not in str(artifact)
    assert "paid" not in str(artifact)
    assert "$request->validate" not in str(artifact)
    assert "DB::table" not in str(artifact)
    assert "$schedule->command" not in str(artifact)
    assert "middlewareAliases" not in str(artifact)
    assert "middlewareGroups" not in str(artifact)
    assert "@extends" not in str(artifact)
    assert "@include" not in str(artifact)
    assert "<x-alert" not in str(artifact)
    assert "@livewire" not in str(artifact)


def test_populate_backend_ast_extracts_symfony_php_graph_without_source(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    workspace = tmp_path / "symfony"
    workspace.mkdir()
    (workspace / "bin").mkdir()
    (workspace / "bin" / "console").write_text("#!/usr/bin/env php\n", encoding="utf-8")
    (workspace / "src" / "Controller").mkdir(parents=True)
    (workspace / "src" / "Service").mkdir(parents=True)
    (workspace / "src" / "Controller" / "OrderController.php").write_text(
        "<?php\n"
        "namespace App\\Controller;\n"
        "use App\\Service\\OrderService;\n"
        "use Symfony\\Component\\HttpFoundation\\Response;\n"
        "use Symfony\\Component\\Routing\\Attribute\\Route;\n"
        "#[Route('/admin', name: 'admin_')]\n"
        "class OrderController {\n"
        "    #[Route('/orders/{id}', name: 'orders_show', methods: ['GET'])]\n"
        "    public function show(OrderService $orders): Response {\n"
        "        return new Response('order');\n"
        "    }\n"
        "    /**\n"
        "     * @Route(\"/legacy\", name=\"legacy_index\", methods={\"POST\"})\n"
        "     */\n"
        "    public function legacy(): Response {\n"
        "        return new Response('legacy');\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    (workspace / "src" / "Controller" / "HealthController.php").write_text(
        "<?php\n"
        "namespace App\\Controller;\n"
        "use Symfony\\Component\\HttpFoundation\\Response;\n"
        "use Symfony\\Component\\Routing\\Attribute\\Route;\n"
        "#[Route('/health', name: 'health_check', methods: ['GET'])]\n"
        "class HealthController {\n"
        "    public function __invoke(): Response {\n"
        "        return new Response('ok');\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    (workspace / "src" / "Service" / "OrderService.php").write_text(
        "<?php\n"
        "namespace App\\Service;\n"
        "class OrderService {}\n",
        encoding="utf-8",
    )

    result = execute_job(
        {
            "job_id": "job_symfony_graph",
            "capability": "populate_backend_ast",
            "payload": {"max_files": 20, "max_symbols": 30, "max_edges": 30},
        },
        workspace_root=workspace,
    )

    artifact = result["artifact"]
    routes = {(item["method"], item["uri"], item["handler"], item.get("name")) for item in artifact["routes"]}
    symbols = {(item["kind"], item["name"]) for item in artifact["symbols"]}
    edges = {(item["kind"], item["from"], item["to"]) for item in artifact["edges"]}

    assert result["status"] == "completed"
    assert artifact["schema"] == "hades.php_graph.v1"
    assert artifact["framework"] == "symfony"
    assert artifact["raw_source_included"] is False
    assert ("GET", "/admin/orders/{id}", "OrderController@show", "admin_orders_show") in routes
    assert ("POST", "/admin/legacy", "OrderController@legacy", "admin_legacy_index") in routes
    assert ("GET", "/health", "HealthController@__invoke", "health_check") in routes
    assert ("class", "App\\Controller\\OrderController") in symbols
    assert ("class", "App\\Controller\\HealthController") in symbols
    assert ("class", "App\\Service\\OrderService") in symbols
    assert ("method", "OrderController@show") in symbols
    assert ("method", "OrderController@legacy") in symbols
    assert ("method", "HealthController@__invoke") in symbols
    assert ("route_handler", "route:admin_orders_show", "OrderController@show") in edges
    assert ("route_handler", "route:admin_legacy_index", "OrderController@legacy") in edges
    assert ("route_handler", "route:health_check", "HealthController@__invoke") in edges
    assert ("uses_dependency", "OrderController@show", "App\\Service\\OrderService") in edges
    assert "#[Route" not in str(artifact)
    assert "@Route" not in str(artifact)
    assert "return new Response" not in str(artifact)


def test_populate_backend_ast_extracts_doctrine_schema_graph_without_source(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    workspace = tmp_path / "doctrine"
    workspace.mkdir()
    (workspace / "src" / "Entity").mkdir(parents=True)
    (workspace / "src" / "Entity" / "Order.php").write_text(
        "<?php\n"
        "namespace App\\Entity;\n"
        "use Doctrine\\ORM\\Mapping as ORM;\n"
        "#[ORM\\Entity]\n"
        "#[ORM\\Table(name: 'customers')]\n"
        "class Customer {\n"
        "    #[ORM\\Id]\n"
        "    #[ORM\\Column(type: 'integer')]\n"
        "    private int $id;\n"
        "    #[ORM\\Column(type: 'string', length: 255, unique: true)]\n"
        "    private string $email;\n"
        "}\n"
        "#[ORM\\Entity]\n"
        "#[ORM\\Table(name: 'orders')]\n"
        "class Order {\n"
        "    #[ORM\\Id]\n"
        "    #[ORM\\Column(type: 'integer')]\n"
        "    private int $id;\n"
        "    #[ORM\\ManyToOne(targetEntity: Customer::class)]\n"
        "    #[ORM\\JoinColumn(name: 'customer_id', referencedColumnName: 'id', nullable: false)]\n"
        "    private Customer $customer;\n"
        "    #[ORM\\Column(type: 'decimal', precision: 10, scale: 2, nullable: true)]\n"
        "    private string $total;\n"
        "}\n",
        encoding="utf-8",
    )

    result = execute_job(
        {
            "job_id": "job_doctrine_graph",
            "capability": "populate_backend_ast",
            "payload": {"max_files": 10, "max_symbols": 20, "max_edges": 20},
        },
        workspace_root=workspace,
    )

    artifact = result["artifact"]
    tables = {item["table"]: item for item in artifact["database"]["tables"]}
    symbols = {(item["kind"], item["name"]) for item in artifact["symbols"]}
    edges = {(item["kind"], item["from"], item["to"]) for item in artifact["edges"]}

    assert result["status"] == "completed"
    assert artifact["schema"] == "hades.php_graph.v1"
    assert artifact["framework"] == "doctrine"
    assert artifact["raw_source_included"] is False
    assert ("class", "App\\Entity\\Customer") in symbols
    assert ("class", "App\\Entity\\Order") in symbols
    assert set(tables) == {"customers", "orders"}
    assert {column["name"] for column in tables["orders"]["columns"]} == {"id", "customer_id", "total"}
    assert tables["orders"]["foreign_keys"] == [
        {
            "table": "orders",
            "column": "customer_id",
            "references_table": "customers",
            "references_column": "id",
            "path": "src/Entity/Order.php",
            "line": 21,
            "nullable": False,
        }
    ]
    assert ("model_table", "App\\Entity\\Order", "table:orders") in edges
    assert ("foreign_key", "table:orders.customer_id", "table:customers") in edges
    assert "#[ORM" not in str(artifact)
    assert "ORM\\Column" not in str(artifact)
    assert "ORM\\JoinColumn" not in str(artifact)


def test_populate_backend_ast_extracts_node_react_code_graph_without_source(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "app" / "api" / "orders").mkdir(parents=True)
    (workspace / "app" / "orders").mkdir(parents=True)
    (workspace / "server").mkdir()
    (workspace / "components").mkdir()
    (workspace / "package.json").write_text(
        '{"dependencies":{"next":"latest","react":"latest","express":"latest"}}',
        encoding="utf-8",
    )
    (workspace / "app" / "api" / "orders" / "route.ts").write_text(
        "import { listOrders } from '../../../server/orders';\n"
        "export async function GET() { return listOrders(); }\n",
        encoding="utf-8",
    )
    (workspace / "app" / "orders" / "page.tsx").write_text(
        "import { OrderTable } from '../../components/OrderTable';\n"
        "export default function OrdersPage() { return <OrderTable />; }\n",
        encoding="utf-8",
    )
    (workspace / "server" / "api.ts").write_text(
        "import express from 'express';\n"
        "const router = express.Router();\n"
        "router.get('/health', healthHandler);\n"
        "export function healthHandler() { console.warn('health degraded'); return { ok: true }; }\n",
        encoding="utf-8",
    )
    (workspace / "components" / "OrderTable.tsx").write_text(
        "export const OrderTable = () => <table />;\n",
        encoding="utf-8",
    )
    (workspace / "components" / "__tests__").mkdir()
    (workspace / "components" / "__tests__" / "OrderTable.test.tsx").write_text(
        "import { OrderTable } from '../OrderTable';\n"
        "test('renders order table', () => expect(OrderTable).toBeDefined());\n",
        encoding="utf-8",
    )

    result = execute_job(
        {
            "job_id": "job_ts_graph",
            "capability": "populate_backend_ast",
            "payload": {"max_files": 50, "max_symbols": 50, "max_edges": 50},
        },
        workspace_root=workspace,
    )

    artifact = result["artifact"]
    routes = {(item["framework"], item["method"], item["path"]) for item in artifact["routes"]}
    symbols = {(item["kind"], item["name"], item["path"]) for item in artifact["symbols"]}
    edges = {(item["kind"], item["from"], item["to"]) for item in artifact["edges"]}
    test_files = {item["path"]: item for item in artifact["tests"]["files"]}
    log_events = {item["context"]: item for item in artifact["logs"]["events"]}

    assert result["status"] == "completed"
    assert artifact["schema"] == "hades.code_graph.v1"
    assert artifact["language"] == "typescript"
    assert artifact["framework"] == "nextjs"
    assert artifact["raw_source_included"] is False
    assert ("nextjs", "GET", "/api/orders") in routes
    assert ("nextjs", "PAGE", "/orders") in routes
    assert ("express", "GET", "/health") in routes
    assert ("component", "OrdersPage", "app/orders/page.tsx") in symbols
    assert ("component", "OrderTable", "components/OrderTable.tsx") in symbols
    assert ("function", "healthHandler", "server/api.ts") in symbols
    assert ("emits_log", "server/api.ts", log_events["server/api.ts"]["id"]) in edges
    assert ("imports", "app/api/orders/route.ts", "../../../server/orders") in edges
    assert artifact["tests"]["schema"] == "hades.test_map.v1"
    assert artifact["tests"]["file_count"] == 1
    assert test_files["components/__tests__/OrderTable.test.tsx"]["test_count"] == 1
    assert test_files["components/__tests__/OrderTable.test.tsx"]["symbol_refs"] == ["OrderTable"]
    assert ("test_covers_symbol", "test:components/__tests__/OrderTable.test.tsx", "OrderTable") in edges
    assert ("test_imports", "test:components/__tests__/OrderTable.test.tsx", "../OrderTable") in edges
    assert artifact["logs"]["schema"] == "hades.log_map.v1"
    assert artifact["logs"]["event_count"] == 1
    assert log_events["server/api.ts"]["level"] == "warning"
    assert log_events["server/api.ts"]["logger"] == "console"
    assert len(log_events["server/api.ts"]["message_sha256"]) == 64
    assert "return listOrders()" not in str(artifact)
    assert "<OrderTable" not in str(artifact)
    assert "<table" not in str(artifact)
    assert "renders order table" not in str(artifact)
    assert "health degraded" not in str(artifact)


def test_populate_backend_ast_extracts_prisma_schema_graph_without_source(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    (tmp_path / "prisma").mkdir()
    (tmp_path / "prisma" / "schema.prisma").write_text(
        "model Customer {\n"
        "  id Int @id @default(autoincrement())\n"
        "  email String @unique\n"
        "  orders Order[]\n"
        "  @@map(\"customers\")\n"
        "}\n"
        "model Order {\n"
        "  id Int @id\n"
        "  customerId Int\n"
        "  customer Customer @relation(fields: [customerId], references: [id])\n"
        "  total Decimal?\n"
        "  @@map(\"orders\")\n"
        "}\n",
        encoding="utf-8",
    )

    result = execute_job(
        {
            "job_id": "job_prisma_schema",
            "capability": "populate_backend_ast",
            "payload": {"max_files": 10, "max_symbols": 10, "max_edges": 10},
        },
        workspace_root=tmp_path,
    )

    artifact = result["artifact"]
    tables = {item["table"]: item for item in artifact["database"]["tables"]}
    symbols = {(item["kind"], item["name"]) for item in artifact["symbols"]}
    edges = {(item["kind"], item["from"], item["to"]) for item in artifact["edges"]}

    assert result["status"] == "completed"
    assert artifact["schema"] == "hades.code_graph.v1"
    assert artifact["language"] == "prisma"
    assert artifact["framework"] == "prisma"
    assert artifact["routes"] == []
    assert ("model", "Customer") in symbols
    assert ("model", "Order") in symbols
    assert set(tables) == {"customers", "orders"}
    assert {column["name"] for column in tables["orders"]["columns"]} == {"id", "customerId", "total"}
    assert tables["orders"]["foreign_keys"] == [
        {
            "table": "orders",
            "column": "customerId",
            "references_table": "customers",
            "references_column": "id",
            "path": "prisma/schema.prisma",
            "line": 10,
        }
    ]
    assert ("model_table", "Order", "table:orders") in edges
    assert ("foreign_key", "table:orders.customerId", "table:customers") in edges
    assert "model Order" not in str(artifact)
    assert "@relation" not in str(artifact)
    assert "@@map" not in str(artifact)


def test_populate_backend_ast_extracts_drizzle_schema_graph_without_source(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    (tmp_path / "db").mkdir()
    (tmp_path / "db" / "schema.ts").write_text(
        "import { integer, numeric, pgTable, serial, text, varchar } from 'drizzle-orm/pg-core';\n"
        "export const customers = pgTable('customers', {\n"
        "  id: serial('id').primaryKey(),\n"
        "  email: varchar('email', { length: 255 }).notNull().unique(),\n"
        "});\n"
        "export const orders = pgTable('orders', {\n"
        "  id: serial('id').primaryKey(),\n"
        "  customerId: integer('customer_id').notNull().references(() => customers.id),\n"
        "  status: text('status').default('draft'),\n"
        "  total: numeric('total'),\n"
        "});\n",
        encoding="utf-8",
    )

    result = execute_job(
        {
            "job_id": "job_drizzle_schema",
            "capability": "populate_backend_ast",
            "payload": {"max_files": 10, "max_symbols": 20, "max_edges": 20},
        },
        workspace_root=tmp_path,
    )

    artifact = result["artifact"]
    tables = {item["table"]: item for item in artifact["database"]["tables"]}
    symbols = {(item["kind"], item["name"]) for item in artifact["symbols"]}
    edges = {(item["kind"], item["from"], item["to"]) for item in artifact["edges"]}

    assert result["status"] == "completed"
    assert artifact["schema"] == "hades.code_graph.v1"
    assert artifact["language"] == "typescript"
    assert artifact["framework"] == "drizzle"
    assert artifact["routes"] == []
    assert ("model", "customers") in symbols
    assert ("model", "orders") in symbols
    assert set(tables) == {"customers", "orders"}
    assert {column["name"] for column in tables["orders"]["columns"]} == {"id", "customer_id", "status", "total"}
    assert tables["orders"]["foreign_keys"] == [
        {
            "table": "orders",
            "column": "customer_id",
            "references_table": "customers",
            "references_column": "id",
            "path": "db/schema.ts",
            "line": 8,
        }
    ]
    assert ("model_table", "orders", "table:orders") in edges
    assert ("foreign_key", "table:orders.customer_id", "table:customers") in edges
    assert "pgTable(" not in str(artifact)
    assert ".references(" not in str(artifact)
    assert "default('draft')" not in str(artifact)


def test_populate_backend_ast_extracts_sql_schema_graph_without_source(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    (tmp_path / "schema.sql").write_text(
        "CREATE TABLE customers (\n"
        "  id INTEGER PRIMARY KEY,\n"
        "  email VARCHAR(255) NOT NULL UNIQUE\n"
        ");\n"
        "CREATE TABLE orders (\n"
        "  id INTEGER PRIMARY KEY,\n"
        "  customer_id INTEGER NOT NULL REFERENCES customers(id),\n"
        "  total DECIMAL(10, 2),\n"
        "  CONSTRAINT orders_customer_fk FOREIGN KEY (customer_id) REFERENCES customers(id)\n"
        ");\n",
        encoding="utf-8",
    )

    result = execute_job(
        {
            "job_id": "job_sql_schema",
            "capability": "populate_backend_ast",
            "payload": {"max_files": 10, "max_symbols": 10, "max_edges": 10},
        },
        workspace_root=tmp_path,
    )

    artifact = result["artifact"]
    tables = {item["table"]: item for item in artifact["database"]["tables"]}
    edges = {(item["kind"], item["from"], item["to"]) for item in artifact["edges"]}

    assert result["status"] == "completed"
    assert artifact["schema"] == "hades.code_graph.v1"
    assert artifact["language"] == "sql"
    assert artifact["framework"] == "sql"
    assert set(tables) == {"customers", "orders"}
    assert {column["name"] for column in tables["orders"]["columns"]} >= {"id", "customer_id", "total"}
    assert tables["orders"]["foreign_keys"] == [
        {
            "table": "orders",
            "column": "customer_id",
            "references_table": "customers",
            "references_column": "id",
            "path": "schema.sql",
            "line": 7,
        },
        {
            "table": "orders",
            "column": "customer_id",
            "references_table": "customers",
            "references_column": "id",
            "path": "schema.sql",
            "line": 9,
        },
    ]
    assert ("schema_table", "schema.sql", "table:orders") in edges
    assert ("foreign_key", "table:orders.customer_id", "table:customers") in edges
    assert "CREATE TABLE" not in str(artifact)
    assert "CONSTRAINT orders_customer_fk" not in str(artifact)


def test_populate_backend_ast_omits_symlinked_python_escape(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    workspace = tmp_path / "repo"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (workspace / "local.py").write_text("class Local:\n    pass\n", encoding="utf-8")
    (outside / "external.py").write_text("class Outside:\n    pass\n", encoding="utf-8")
    _symlink_or_skip(workspace / "external.py", outside / "external.py")

    result = execute_job(
        {
            "job_id": "job_ast_symlink",
            "capability": "populate_backend_ast",
            "payload": {"max_files": 20, "max_symbols": 20},
        },
        workspace_root=workspace,
    )

    artifact = result["artifact"]
    names = {item["name"] for item in artifact["symbols"]}
    omitted = {item["path"]: item["reason"] for item in artifact["omitted"]}

    assert "Local" in names
    assert "Outside" not in names
    assert omitted["external.py"] == "symlink"
    assert "Outside" not in str(result)


def test_populate_backend_ast_omits_per_file_read_errors(monkeypatch, tmp_path):
    import hermes_cli.hades_backend_jobs as jobs

    (tmp_path / "good.py").write_text("class Good:\n    pass\n", encoding="utf-8")
    (tmp_path / "bad.py").write_text("class Bad:\n    pass\n", encoding="utf-8")
    original_read_text_bounded = jobs._read_text_bounded

    def fake_read_text_bounded(path, max_bytes):
        if path.name == "bad.py":
            raise OSError(13, "permission denied")
        return original_read_text_bounded(path, max_bytes)

    monkeypatch.setattr(jobs, "_read_text_bounded", fake_read_text_bounded)

    result = jobs.execute_job(
        {
            "job_id": "job_ast_error",
            "capability": "populate_backend_ast",
            "payload": {"max_files": 10, "max_symbols": 10},
        },
        workspace_root=tmp_path,
    )

    artifact = result["artifact"]
    names = {item["name"] for item in artifact["symbols"]}
    omitted = {item["path"]: item["reason"] for item in artifact["omitted"]}

    assert result["status"] == "completed"
    assert "Good" in names
    assert "Bad" not in names
    assert omitted["bad.py"] == "read_error:13"
