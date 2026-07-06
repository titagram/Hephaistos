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
    (workspace / "app" / "Models").mkdir(parents=True)
    (workspace / "app" / "Services").mkdir(parents=True)
    (workspace / "routes" / "web.php").write_text(
        "<?php\n"
        "use App\\Http\\Controllers\\OrderController;\n"
        "Route::get('/orders/{order}', [OrderController::class, 'show'])->name('orders.show');\n",
        encoding="utf-8",
    )
    (workspace / "app" / "Http" / "Controllers" / "OrderController.php").write_text(
        "<?php\n"
        "namespace App\\Http\\Controllers;\n"
        "use App\\Models\\Order;\n"
        "use App\\Services\\OrderService;\n"
        "class OrderController extends Controller {\n"
        "    public function show(Order $order) {\n"
        "        return OrderService::format($order);\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    (workspace / "app" / "Models" / "Order.php").write_text(
        "<?php\n"
        "namespace App\\Models;\n"
        "use Illuminate\\Database\\Eloquent\\Model;\n"
        "class Order extends Model {\n"
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

    assert result["status"] == "completed"
    assert artifact["schema"] == "hades.php_graph.v1"
    assert artifact["framework"] == "laravel"
    assert artifact["raw_source_included"] is False
    assert ("GET", "/orders/{order}", "OrderController@show") in routes
    assert ("class", "App\\Http\\Controllers\\OrderController") in symbols
    assert ("class", "App\\Models\\Order") in symbols
    assert ("class", "App\\Services\\OrderService") in symbols
    assert ("method", "OrderController@show") in symbols
    assert ("method", "Order@customer") in symbols
    assert ("route_handler", "route:orders.show", "OrderController@show") in edges
    assert ("eloquent_relation", "App\\Models\\Order", "App\\Models\\Customer") in edges
    assert "Route::get" not in str(artifact)
    assert "return $this->belongsTo" not in str(artifact)
    assert "return OrderService::format" not in str(artifact)


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
