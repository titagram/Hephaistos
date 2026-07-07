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


def test_populate_backend_ast_extracts_laravel_php_graph_without_source(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "artisan").write_text("#!/usr/bin/env php\n", encoding="utf-8")
    (workspace / "routes").mkdir()
    (workspace / "app" / "Http" / "Controllers").mkdir(parents=True)
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
    (workspace / "routes" / "web.php").write_text(
        "<?php\n"
        "use App\\Http\\Controllers\\OrderController;\n"
        "Route::get('/orders/{order}', [OrderController::class, 'show'])"
        "->middleware(['auth', 'verified'])->name('orders.show');\n",
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
        "class OrderController extends Controller {\n"
        "    public function __construct(OrderService $orders) {}\n"
        "    public function show(StoreOrderRequest $request, Order $order) {\n"
        "        $request->validate(['status' => 'required|string']);\n"
        "        config('services.orders.cache');\n"
        "        env('ORDER_DEBUG');\n"
        "        SyncOrderJob::dispatch($order->id);\n"
        "        event(new OrderPlaced($order));\n"
        "        DB::table('orders')->join('customers', 'orders.customer_id', '=', 'customers.id')->first();\n"
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

    result = execute_job(
        {
            "job_id": "job_php_graph",
            "capability": "populate_backend_ast",
            "payload": {"max_files": 50, "max_symbols": 50, "max_edges": 50},
        },
        workspace_root=workspace,
    )

    artifact = result["artifact"]
    routes = {(item["method"], item["uri"], item["handler"]) for item in artifact["routes"]}
    symbols = {(item["kind"], item["name"]) for item in artifact["symbols"]}
    edges = {(item["kind"], item["from"], item["to"]) for item in artifact["edges"]}
    tables = {item["table"]: item for item in artifact["database"]["tables"]}

    assert result["status"] == "completed"
    assert artifact["schema"] == "hades.php_graph.v1"
    assert artifact["framework"] == "laravel"
    assert artifact["raw_source_included"] is False
    assert ("GET", "/orders/{order}", "OrderController@show") in routes
    assert ("class", "App\\Http\\Controllers\\OrderController") in symbols
    assert ("class", "App\\Models\\Order") in symbols
    assert ("class", "App\\Policies\\OrderPolicy") in symbols
    assert ("class", "App\\Services\\OrderService") in symbols
    assert ("class", "App\\Http\\Requests\\StoreOrderRequest") in symbols
    assert ("class", "App\\Jobs\\SyncOrderJob") in symbols
    assert ("class", "App\\Events\\OrderPlaced") in symbols
    assert ("class", "App\\Listeners\\SendOrderReceipt") in symbols
    assert ("class", "App\\Console\\Commands\\SyncOrdersCommand") in symbols
    assert ("interface", "App\\Contracts\\OrderFormatter") in symbols
    assert ("class", "App\\Observers\\OrderObserver") in symbols
    assert ("class", "App\\Broadcasting\\OrderChannel") in symbols
    assert ("table", "table:orders") in symbols
    assert ("method", "OrderController@__construct") in symbols
    assert ("method", "OrderController@show") in symbols
    assert ("method", "Order@customer") in symbols
    assert ("route_handler", "route:orders.show", "OrderController@show") in edges
    assert ("route_middleware", "route:orders.show", "middleware:auth") in edges
    assert ("route_middleware", "route:orders.show", "middleware:verified") in edges
    assert ("eloquent_relation", "App\\Models\\Order", "App\\Models\\Customer") in edges
    assert ("static_call", "App\\Http\\Controllers\\OrderController", "App\\Services\\OrderService::format") in edges
    assert ("uses_form_request", "OrderController@show", "App\\Http\\Requests\\StoreOrderRequest") in edges
    assert ("uses_dependency", "OrderController@__construct", "App\\Services\\OrderService") in edges
    assert ("uses_dependency", "OrderController@show", "App\\Models\\Order") in edges
    assert ("request_validation", "App\\Http\\Requests\\StoreOrderRequest", "validation:customer_id") in edges
    assert ("request_validation", "App\\Http\\Controllers\\OrderController", "validation:status") in edges
    assert ("dispatches_job", "App\\Http\\Controllers\\OrderController", "App\\Jobs\\SyncOrderJob") in edges
    assert ("emits_event", "App\\Http\\Controllers\\OrderController", "App\\Events\\OrderPlaced") in edges
    assert ("event_listener", "App\\Events\\OrderPlaced", "App\\Listeners\\SendOrderReceipt") in edges
    assert ("artisan_command", "App\\Console\\Commands\\SyncOrdersCommand", "command:orders:sync") in edges
    assert ("scheduled_command", "App\\Console\\Kernel", "command:orders:sync") in edges
    assert ("scheduled_job", "App\\Console\\Kernel", "App\\Jobs\\SyncOrderJob") in edges
    assert ("query_table", "App\\Http\\Controllers\\OrderController", "table:orders") in edges
    assert ("query_table", "App\\Http\\Controllers\\OrderController", "table:customers") in edges
    assert ("eloquent_query", "App\\Http\\Controllers\\OrderController", "App\\Models\\Order::where") in edges
    assert ("view_ref", "App\\Http\\Controllers\\OrderController", "view:orders.show") in edges
    assert ("model_table", "App\\Models\\Order", "table:orders") in edges
    assert ("policy_for", "App\\Models\\Order", "App\\Policies\\OrderPolicy") in edges
    assert ("container_binding", "App\\Contracts\\OrderFormatter", "App\\Services\\OrderService") in edges
    assert ("observed_by", "App\\Models\\Order", "App\\Observers\\OrderObserver") in edges
    assert ("broadcast_channel", "routes/channels.php", "broadcast:orders.{order}") in edges
    assert ("config_ref", "App\\Http\\Controllers\\OrderController", "config:services.orders.cache") in edges
    assert ("env_ref", "App\\Http\\Controllers\\OrderController", "env:ORDER_DEBUG") in edges
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
    assert "Route::get" not in str(artifact)
    assert "return $this->belongsTo" not in str(artifact)
    assert "return OrderService::format" not in str(artifact)
    assert "return view('orders.show'" not in str(artifact)
    assert "$this->app->singleton" not in str(artifact)
    assert "Order::observe" not in str(artifact)
    assert "Broadcast::channel" not in str(artifact)
    assert "Schema::create" not in str(artifact)
    assert "config('services.orders.cache')" not in str(artifact)
    assert "$request->validate" not in str(artifact)
    assert "DB::table" not in str(artifact)
    assert "$schedule->command" not in str(artifact)


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
        "export function healthHandler() { return { ok: true }; }\n",
        encoding="utf-8",
    )
    (workspace / "components" / "OrderTable.tsx").write_text(
        "export const OrderTable = () => <table />;\n",
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
    assert ("imports", "app/api/orders/route.ts", "../../../server/orders") in edges
    assert "return listOrders()" not in str(artifact)
    assert "<OrderTable" not in str(artifact)
    assert "<table" not in str(artifact)


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
