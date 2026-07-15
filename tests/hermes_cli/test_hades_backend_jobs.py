from __future__ import annotations

import subprocess

import pytest


def _symlink_or_skip(link, target, *, target_is_directory=False):
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable in test environment: {exc}")


def test_php_validation_database_rule_refs_keep_only_sanitized_identifiers():
    from hermes_cli.hades_index.php import _php_validation_database_rule_refs

    refs = _php_validation_database_rule_refs(
        "'required|unique:orders,status|exists:tenant.customers,id|exists:customers,id,deleted_at,NULL', "
        "Rule::exists('warehouses', 'id'), Rule::unique('products', 'sku')"
    )

    assert refs == [
        {"rule": "unique", "table": "orders", "column": "status"},
        {"rule": "exists", "table": "customers", "column": "id"},
        {"rule": "exists", "table": "warehouses", "column": "id"},
        {"rule": "unique", "table": "products", "column": "sku"},
    ]


def test_php_top_level_array_field_keys_ignores_nested_keys():
    from hermes_cli.hades_index.php import _php_top_level_array_field_keys

    source = "$this->merge(['filters' => ['status' => 'paid'], 'status' => 'paid']);"
    args_start = source.index("(") + 1
    args = source[args_start : source.rindex(")")]

    fields = _php_top_level_array_field_keys(source, args, args_start)

    assert [field["field"] for field in fields] == ["filters", "status"]


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
                "candidate_key": "a" * 64,
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
    assert source_slice["candidate_key"] == "a" * 64
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
    assert result["artifact"]["workspace_state"]["schema"] == "hades.workspace_state.v1"
    assert len(result["artifact"]["workspace_state"]["content_fingerprint"]) == 64
    assert result["artifact"]["workspace_state"]["file_count"] == len(result["artifact"]["files"])
    assert "pyproject.toml" in paths
    assert "src/app.py" in paths
    assert all(not path.startswith(".git") for path in paths)


def test_sync_git_tree_includes_dirty_worktree_metadata_without_sensitive_paths(tmp_path):
    try:
        subprocess.run(["git", "--version"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:
        pytest.skip(f"git unavailable: {exc}")

    from hermes_cli.hades_backend_jobs import execute_job

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def run():\n    return 'raw source'\n", encoding="utf-8")
    (tmp_path / ".env").write_text("TOKEN=secret\n", encoding="utf-8")

    result = execute_job(
        {
            "job_id": "job_dirty_tree",
            "capability": "sync_git_tree",
            "payload": {"max_files": 10, "max_bytes": 20_000, "head_commit": "abc123"},
        },
        workspace_root=tmp_path,
    )

    artifact = result["artifact"]
    state = artifact["workspace_state"]

    assert result["status"] == "completed"
    assert state["head_commit"] == "abc123"
    assert state["git_status"]["available"] is True
    assert state["git_status"]["dirty"] is True
    assert "src/app.py" in state["git_status"]["changed_paths"]
    assert ".env" not in state["git_status"]["changed_paths"]
    assert "raw source" not in str(artifact)


def test_workspace_file_iteration_prioritizes_source_dirs_before_assets(tmp_path):
    from hermes_cli.hades_backend_jobs import _iter_workspace_files

    assets = tmp_path / "assets"
    assets.mkdir()
    for index in range(20):
        (assets / f"style_{index}.scss").write_text(".x { color: red; }\n", encoding="utf-8")
    (tmp_path / ".devboard" / "artifacts").mkdir(parents=True)
    (tmp_path / ".devboard" / "artifacts" / "graph.json").write_text("{}", encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    (src / "Controller.php").write_text("<?php\nclass Controller {}\n", encoding="utf-8")

    files, omitted, truncated = _iter_workspace_files(tmp_path, max_files=5)

    paths = [path.relative_to(tmp_path).as_posix() for path in files]
    assert "src/Controller.php" in paths
    assert truncated is True
    assert {"path": ".devboard", "reason": "generated_or_dependency_dir"} in omitted


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


def test_populate_backend_ast_includes_source_slice_candidates_for_laravel(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    (tmp_path / "artisan").write_text("#!/usr/bin/env php\n", encoding="utf-8")
    controller = tmp_path / "app" / "Http" / "Controllers" / "BookingController.php"
    controller.parent.mkdir(parents=True)
    controller.write_text(
        "<?php\nclass BookingController {\n public function store() { return Booking::create([]); }\n}\n",
        encoding="utf-8",
    )
    model = tmp_path / "app" / "Models" / "Booking.php"
    model.parent.mkdir(parents=True)
    model.write_text("<?php\nclass Booking extends Model {}\n", encoding="utf-8")

    result = execute_job(
        {
            "job_id": "job_ast",
            "capability": "populate_backend_ast",
            "payload": {"max_files": 50, "max_symbols": 100, "head_commit": "abc123"},
        },
        workspace_root=tmp_path,
    )

    artifact = result["artifact"]
    assert result["status"] == "completed"
    assert artifact["schema"] == "hades.php_graph.v1"
    assert artifact["graph_contract"]["version"] == "hades.graph_artifact.v1"
    assert artifact["graph_contract"]["extractor"]["name"] == "hades-native-php"
    assert artifact["graph_contract"]["source"]["head_commit"] == "abc123"
    assert artifact["source_slice_candidates"]
    assert {item["reason"] for item in artifact["source_slice_candidates"]} >= {"laravel_controller", "eloquent_model"}
    assert "source_slice_candidates:" in result["summary"]
    assert "return Booking::create" not in str(artifact["source_slice_candidates"])


def test_populate_backend_ast_combines_php_and_typescript_in_one_polyglot_graph(
    tmp_path,
):
    from hermes_cli.hades_backend_jobs import execute_job

    php = tmp_path / "src" / "PhpController.php"
    php.parent.mkdir(parents=True)
    php.write_text(
        "<?php\nnamespace App;\nclass PhpController {}\n",
        encoding="utf-8",
    )
    typescript = tmp_path / "server" / "api.ts"
    typescript.parent.mkdir(parents=True)
    typescript.write_text(
        "import express from 'express';\n"
        "const router = express.Router();\n"
        "router.get('/health', healthHandler);\n"
        "export function healthHandler() { return { ok: true }; }\n",
        encoding="utf-8",
    )

    result = execute_job(
        {
            "job_id": "job_polyglot_graph",
            "capability": "populate_backend_ast",
            "payload": {"max_files": 20, "max_symbols": 50, "max_edges": 50},
        },
        workspace_root=tmp_path,
    )

    artifact = result["artifact"]
    assert result["status"] == "completed"
    assert artifact["language"] == "polyglot"
    assert artifact["graph_contract"]["coverage"]["languages"] == [
        "php",
        "typescript",
    ]
    assert {item.get("name") for item in artifact["symbols"]} >= {
        "App\\PhpController",
        "healthHandler",
    }
    assert any(
        route.get("framework") == "express"
        and route.get("method") == "GET"
        and route.get("path") == "/health"
        for route in artifact["routes"]
    )
    assert any(
        node.get("kind") == "route" and node.get("uri") == "/health"
        for node in artifact["nodes"]
    )


def test_populate_backend_ast_polyglot_coverage_deduplicates_adapter_failures(
    tmp_path,
):
    from hermes_cli.hades_backend_jobs import execute_job

    workspace = tmp_path / "workspace"
    source = workspace / "src"
    source.mkdir(parents=True)
    source.joinpath("Controller.php").write_text(
        "<?php\nclass Controller {}\n",
        encoding="utf-8",
    )
    oversized = source / "oversized.ts"
    with oversized.open("wb") as handle:
        handle.truncate(512_001)

    result = execute_job(
        {
            "job_id": "job_polyglot_coverage",
            "capability": "populate_backend_ast",
            "payload": {},
        },
        workspace_root=workspace,
    )

    artifact = result["artifact"]
    coverage = artifact["graph_contract"]["coverage"]
    assert artifact["omitted"] == [
        {"path": "src/oversized.ts", "reason": "file_too_large"}
    ]
    assert coverage["files_total"] == 2
    assert coverage["files_analyzed"] == 1
    assert coverage["files_failed"] == 1


def test_populate_backend_ast_default_file_budget_exceeds_one_thousand(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    source = tmp_path / "src"
    source.mkdir()
    for index in range(1_001):
        (source / f"class_{index:04d}.py").write_text(
            f"class Class{index}:\n    pass\n",
            encoding="utf-8",
        )

    result = execute_job(
        {
            "job_id": "job_large_ast",
            "capability": "populate_backend_ast",
            "payload": {"max_symbols": 2_000, "max_edges": 2_000},
        },
        workspace_root=tmp_path,
    )

    artifact = result["artifact"]
    assert "Class1000" in {item.get("name") for item in artifact["symbols"]}
    assert artifact["graph_contract"]["coverage"]["files_analyzed"] == 1_001
    assert not any(
        item.get("reason") == "file_budget_exceeded"
        for item in artifact.get("omitted", [])
    )


def test_populate_backend_ast_hard_clamps_oversized_file_budget(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    workspace = tmp_path / "workspace"
    source = workspace / "src"
    source.mkdir(parents=True)
    for index in range(10_001):
        (source / f"module_{index:05d}.py").touch()

    result = execute_job(
        {
            "job_id": "job_hard_file_cap",
            "capability": "populate_backend_ast",
            "payload": {"max_files": 20_000},
        },
        workspace_root=workspace,
    )

    coverage = result["artifact"]["graph_contract"]["coverage"]
    assert coverage["files_analyzed"] == 10_000
    assert coverage["files_budget_omitted"] == 1
    assert result["artifact"]["truncated"] is True


def test_populate_backend_ast_hard_clamps_oversized_aggregate_byte_budget(
    tmp_path,
):
    from hermes_cli.hades_backend_jobs import execute_job

    workspace = tmp_path / "workspace"
    source = workspace / "src"
    source.mkdir(parents=True)
    oversized = source / "oversized.py"
    with oversized.open("wb") as handle:
        handle.truncate(134_217_729)

    result = execute_job(
        {
            "job_id": "job_hard_byte_cap",
            "capability": "populate_backend_ast",
            "payload": {"max_total_bytes": 268_435_456},
        },
        workspace_root=workspace,
    )

    artifact = result["artifact"]
    coverage = artifact["graph_contract"]["coverage"]
    assert coverage["files_analyzed"] == 0
    assert coverage["files_budget_omitted"] == 1
    assert artifact["omitted"] == [
        {"path": "src/oversized.py", "reason": "byte_budget_exceeded"}
    ]


def test_populate_backend_ast_hard_clamps_oversized_per_file_budget(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    workspace = tmp_path / "workspace"
    source = workspace / "src"
    source.mkdir(parents=True)
    oversized = source / "oversized.py"
    with oversized.open("wb") as handle:
        handle.truncate(512_001)

    result = execute_job(
        {
            "job_id": "job_hard_per_file_cap",
            "capability": "populate_backend_ast",
            "payload": {"max_file_bytes": 1_024_000},
        },
        workspace_root=workspace,
    )

    assert result["artifact"]["omitted"] == [
        {"path": "src/oversized.py", "reason": "file_too_large"}
    ]


def test_populate_backend_ast_counts_single_language_test_inventory_truncation(
    tmp_path,
):
    from hermes_cli.hades_backend_jobs import execute_job

    workspace = tmp_path / "workspace"
    tests = workspace / "tests"
    tests.mkdir(parents=True)
    for index in range(501):
        tests.joinpath(f"test_behavior_{index:03d}.py").write_text(
            f"def test_behavior_{index}():\n    pass\n",
            encoding="utf-8",
        )

    artifact = execute_job(
        {
            "job_id": "job_single_language_test_inventory_cap",
            "capability": "populate_backend_ast",
            "payload": {},
        },
        workspace_root=workspace,
    )["artifact"]

    coverage = artifact["graph_contract"]["coverage"]
    assert len(artifact["tests"]["files"]) == 500
    assert artifact["tests"]["truncated"] is True
    assert coverage["tests_promoted"] == 500
    assert coverage["tests_omitted"] == 1


def test_populate_backend_ast_counts_single_language_route_inventory_truncation(
    tmp_path,
):
    from hermes_cli.hades_backend_jobs import execute_job

    workspace = tmp_path / "workspace"
    source = workspace / "src"
    source.mkdir(parents=True)
    source.joinpath("routes.ts").write_text(
        "import express from 'express';\n"
        "const router = express.Router();\n"
        + "".join(
            f"router.get('/route/{index}', handler{index});\n"
            for index in range(501)
        ),
        encoding="utf-8",
    )

    artifact = execute_job(
        {
            "job_id": "job_single_language_route_inventory_cap",
            "capability": "populate_backend_ast",
            "payload": {},
        },
        workspace_root=workspace,
    )["artifact"]

    coverage = artifact["graph_contract"]["coverage"]
    assert len(artifact["routes"]) == 500
    assert coverage["routes_promoted"] == 500
    assert coverage["routes_omitted"] == 1


def test_populate_backend_ast_preserves_polyglot_inventory_cap_coverage(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    workspace = tmp_path / "workspace"
    source = workspace / "src"
    source.mkdir(parents=True)
    source.joinpath("routes.ts").write_text(
        "import express from 'express';\n"
        "const router = express.Router();\n"
        + "".join(
            f"router.get('/route/{index}', handler{index});\n"
            for index in range(501)
        ),
        encoding="utf-8",
    )
    source.joinpath("marker.py").write_text("value = 1\n", encoding="utf-8")
    tests = workspace / "tests"
    tests.mkdir()
    for index in range(501):
        tests.joinpath(f"behavior_{index:03d}.test.ts").write_text(
            f"test('behavior {index}', () => {{}});\n",
            encoding="utf-8",
        )

    artifact = execute_job(
        {
            "job_id": "job_polyglot_inventory_cap",
            "capability": "populate_backend_ast",
            "payload": {},
        },
        workspace_root=workspace,
    )["artifact"]

    coverage = artifact["graph_contract"]["coverage"]
    assert artifact["language"] == "polyglot"
    assert len(artifact["routes"]) == 500
    assert len(artifact["tests"]["files"]) == 500
    assert coverage["routes_promoted"] == 500
    assert coverage["routes_omitted"] == 1
    assert coverage["tests_promoted"] == 500
    assert coverage["tests_omitted"] == 1
    assert not any(key.startswith("_") for key in artifact)
    assert not any(key.startswith("_") for key in artifact["tests"])


def test_populate_backend_ast_hard_clamps_symbol_and_edge_capacities(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    workspace = tmp_path / "workspace"
    source = workspace / "src"
    source.mkdir(parents=True)
    source.joinpath("graph.py").write_text(
        "".join(f"import module_{index}\n" for index in range(10_001))
        + "".join(f"class Class{index}:\n    pass\n" for index in range(5_001)),
        encoding="utf-8",
    )

    result = execute_job(
        {
            "job_id": "job_hard_graph_caps",
            "capability": "populate_backend_ast",
            "payload": {"max_symbols": 6_000, "max_edges": 12_000},
        },
        workspace_root=workspace,
    )

    artifact = result["artifact"]
    assert len(artifact["symbols"]) == 5_000
    assert len(artifact["edges"]) == 10_000
    assert artifact["truncated"] is True


def test_workspace_file_iteration_reports_byte_budget_separately(tmp_path):
    from hermes_cli.hades_backend_jobs import _iter_workspace_files

    (tmp_path / "a.py").write_text("12345", encoding="utf-8")
    (tmp_path / "b.py").write_text("67890", encoding="utf-8")

    files, omitted, truncated = _iter_workspace_files(
        tmp_path,
        max_files=10,
        max_total_bytes=8,
    )

    assert [path.name for path in files] == ["a.py"]
    assert omitted == [{"path": "b.py", "reason": "byte_budget_exceeded"}]
    assert truncated is True


def test_populate_project_wiki_generates_bounded_wiki_refresh_pages(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    (tmp_path / "composer.json").write_text('{"require":{"laravel/framework":"^11.0"}}\n', encoding="utf-8")
    routes = tmp_path / "routes"
    routes.mkdir()
    routes.joinpath("web.php").write_text(
        "<?php\nuse App\\Http\\Controllers\\BookingController;\nRoute::post('/bookings', [BookingController::class, 'store'])->name('bookings.store');\n",
        encoding="utf-8",
    )
    controller = tmp_path / "app" / "Http" / "Controllers" / "BookingController.php"
    controller.parent.mkdir(parents=True)
    controller.write_text(
        "<?php\nnamespace App\\Http\\Controllers;\nclass BookingController {\n public function store() { return Booking::create([]); }\n}\n",
        encoding="utf-8",
    )
    model = tmp_path / "app" / "Models" / "Booking.php"
    model.parent.mkdir(parents=True)
    model.write_text("<?php\nnamespace App\\Models;\nclass Booking extends Model {}\n", encoding="utf-8")
    migration = tmp_path / "database" / "migrations"
    migration.mkdir(parents=True)
    migration.joinpath("2026_01_01_000000_create_bookings_table.php").write_text(
        "<?php\nSchema::create('bookings', function (Blueprint $table) { $table->id(); $table->string('status'); });\n",
        encoding="utf-8",
    )
    test_file = tmp_path / "tests" / "Feature" / "BookingTest.php"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("<?php\nit('stores bookings', function () { $this->post('/bookings'); });\n", encoding="utf-8")

    result = execute_job(
        {
            "job_id": "job_wiki",
            "capability": "populate_project_wiki",
            "payload": {"max_files": 80, "max_symbols": 200},
        },
        workspace_root=tmp_path,
    )

    pages = result["pages"]
    slugs = {page["slug"] for page in pages}
    overview = pages[0]

    assert result["status"] == "completed"
    assert result["schema"] == "devboard.wiki_refresh_result.v1"
    assert result["raw_source_included"] is False
    assert overview["source_status"] == "verified_from_code"
    assert "Project Overview" in overview["title"]
    assert "Raw source is not embedded" in overview["content_markdown"]
    assert any(ref["kind"] == "artifact_ref" for ref in overview["evidence_refs"])
    assert any(ref.get("path") == "composer.json" for ref in overview["evidence_refs"])
    assert any(slug.endswith("-entrypoints") for slug in slugs)
    assert any(slug.endswith("-data-model") for slug in slugs)
    assert any(slug.endswith("-symbol-map") for slug in slugs)
    assert any(slug.endswith("-tests-quality") for slug in slugs)
    assert "return Booking::create" not in str(result)


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
    assert result["artifact"]["schema"] == "hades.code_graph.v1"
    assert result["artifact"]["framework"] == "python"
    assert result["artifact"]["routes"] == []
    assert result["artifact"]["database"] == {"tables": []}
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
    (workspace / "app" / "Mail").mkdir(parents=True)
    (workspace / "app" / "Notifications").mkdir(parents=True)
    (workspace / "app" / "Console" / "Commands").mkdir(parents=True)
    (workspace / "app" / "Livewire").mkdir(parents=True)
    (workspace / "app" / "Models").mkdir(parents=True)
    (workspace / "app" / "Http" / "Resources").mkdir(parents=True)
    (workspace / "app" / "Policies").mkdir(parents=True)
    (workspace / "app" / "Providers").mkdir(parents=True)
    (workspace / "app" / "Services").mkdir(parents=True)
    (workspace / "app" / "Contracts").mkdir(parents=True)
    (workspace / "app" / "Observers").mkdir(parents=True)
    (workspace / "app" / "Broadcasting").mkdir(parents=True)
    (workspace / "app" / "Exceptions").mkdir(parents=True)
    (workspace / "app" / "View" / "Components" / "Orders").mkdir(parents=True)
    (workspace / "database" / "migrations").mkdir(parents=True)
    (workspace / "resources" / "views" / "orders" / "partials").mkdir(parents=True)
    (workspace / "resources" / "views" / "layouts").mkdir(parents=True)
    (workspace / "resources" / "views" / "shared").mkdir(parents=True)
    (workspace / "resources" / "views" / "components" / "orders").mkdir(parents=True)
    (workspace / "routes" / "web.php").write_text(
        "<?php\n"
        "use App\\Http\\Controllers\\OrderController;\n"
        "use App\\Http\\Controllers\\InvoiceController;\n"
        "Route::get('/orders/{order}', [OrderController::class, 'show'])"
        "->middleware(['web', 'auth', 'verified', 'throttle:60,1'])->name('orders.show');\n"
        "Route::apiResource('invoices', InvoiceController::class)->middleware('auth');\n",
        encoding="utf-8",
    )
    (workspace / "app" / "Http" / "Kernel.php").write_text(
        "<?php\n"
        "namespace App\\Http;\n"
        "use App\\Http\\Middleware\\Authenticate;\n"
        "use App\\Http\\Middleware\\EncryptCookies;\n"
        "use App\\Http\\Middleware\\EnsureEmailIsVerified;\n"
        "use App\\Http\\Middleware\\ThrottleRequests;\n"
        "class Kernel {\n"
        "    protected $middlewareAliases = [\n"
        "        'auth' => Authenticate::class,\n"
        "        'verified' => EnsureEmailIsVerified::class,\n"
        "        'throttle' => ThrottleRequests::class,\n"
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
        "class Authenticate {\n"
        "    public function handle($request, $next) { return $next($request); }\n"
        "}\n",
        encoding="utf-8",
    )
    (workspace / "app" / "Http" / "Middleware" / "EncryptCookies.php").write_text(
        "<?php\n"
        "namespace App\\Http\\Middleware;\n"
        "class EncryptCookies {\n"
        "    public function handle($request, $next) { return $next($request); }\n"
        "}\n",
        encoding="utf-8",
    )
    (workspace / "app" / "Http" / "Middleware" / "EnsureEmailIsVerified.php").write_text(
        "<?php\n"
        "namespace App\\Http\\Middleware;\n"
        "class EnsureEmailIsVerified {\n"
        "    public function handle($request, $next) { return $next($request); }\n"
        "}\n",
        encoding="utf-8",
    )
    (workspace / "app" / "Http" / "Middleware" / "ThrottleRequests.php").write_text(
        "<?php\n"
        "namespace App\\Http\\Middleware;\n"
        "class ThrottleRequests {\n"
        "    public function handle($request, $next) { return $next($request); }\n"
        "}\n",
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
        "    public function __construct(private OrderService $orders) {}\n"
        "    public function show(StoreOrderRequest $request, Order $order, \\App\\Contracts\\OrderFormatter $formatter) {\n"
        "        $this->authorize('view', $order);\n"
        "        $request->validate(['status' => 'required|string']);\n"
        "        config('services.orders.cache');\n"
        "        env('ORDER_DEBUG');\n"
        "        Log::warning('order payment gateway degraded');\n"
        "        SyncOrderJob::dispatch($order->id);\n"
        "        event(new OrderPlaced($order)); "
        "Mail::to($order->customer)->send(new \\App\\Mail\\OrderReceiptMail($order)); "
        "$order->customer->notify(new \\App\\Notifications\\OrderShippedNotification($order));\n"
        "        DB::table('orders')->join('customers', 'orders.customer_id', '=', 'customers.id')->first();\n"
        "        DB::table('orders')->where('status', 'pending')->update(['status' => 'paid']);\n"
        "        Order::where('status', 'paid')->first();\n"
        "        Order::where('status', 'pending')->update(['status' => 'paid']);\n"
        "        Order::recent()->first();\n"
        "        App\\Http\\Resources\\OrderResource::make($order);\n"
        "        OrderService::format($order);\n"
        "        $formatter->format($order);\n"
        "        $this->orders->format($order);\n"
        "        abort_if($order->status === 'archived', 403);\n"
        "        redirect()->route('orders.index', [], 302);\n"
        "        \\Illuminate\\Support\\Facades\\Cache::remember('orders.summary', 60, fn () => 'cached');\n"
        "        session()->flash('orders.notice', 'Order queued');\n"
        "        \\Illuminate\\Support\\Facades\\Http::post('https://api.example.test/orders/sync?token=secret', ['status' => 'paid']);\n"
        "        \\Illuminate\\Support\\Facades\\Storage::disk('s3')->put('orders/export.csv', 'secret csv payload');\n"
        "        $request->input('customer_note', 'private fallback note');\n"
        "        $request->hasFile('invoice_pdf');\n"
        "        \\Illuminate\\Support\\Facades\\Cookie::queue('orders_filter', 'private cookie value');\n"
        "        DB::transaction(function () { DB::table('orders')->update(['status' => 'rolled_back_secret']); });\n"
        "        Order::withTrashed()->where('status', 'archived')->first();\n"
        "        Order::lockForUpdate()->first();\n"
        "        Order::onlyTrashed()->forceDelete();\n"
        "        Order::where('status', 'archived')->restore();\n"
        "        $order->restore();\n"
        "        return view('orders.show', ['order' => $order]);\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    (workspace / "app" / "Http" / "Resources" / "OrderResource.php").write_text(
        "<?php\n"
        "namespace App\\Http\\Resources;\n"
        "use Illuminate\\Http\\Resources\\Json\\JsonResource;\n"
        "class OrderResource extends JsonResource {\n"
        "    public function toArray($request): array {\n"
        "        return ['id' => $this->id, 'status' => $this->status];\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    (workspace / "app" / "Http" / "Controllers" / "InvoiceController.php").write_text(
        "<?php\n"
        "namespace App\\Http\\Controllers;\n"
        "class InvoiceController extends Controller {\n"
        "    public function index() {}\n"
        "    public function store() {}\n"
        "    public function show($invoice) {}\n"
        "    public function update($invoice) { return response()->json(['error' => 'locked'], 409); }\n"
        "    public function destroy($invoice) {}\n"
        "}\n",
        encoding="utf-8",
    )
    (workspace / "app" / "Http" / "Requests" / "StoreOrderRequest.php").write_text(
        "<?php\n"
        "namespace App\\Http\\Requests;\n"
        "use Illuminate\\Foundation\\Http\\FormRequest;\n"
        "class StoreOrderRequest extends FormRequest {\n"
        "    public function rules(): array {\n"
        "        return ['customer_id' => 'required|integer|exists:customers,id', 'status' => 'required|string'];\n"
        "    }\n"
        "    public function authorize(): bool { return false; }\n"
        "    public function prepareForValidation(): void {\n"
        "        $this->merge(['status' => strtolower($this->input('status')), 'customer_id' => (int) $this->customer_id]);\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    (workspace / "app" / "Livewire" / "OrdersStatus.php").write_text(
        "<?php\n"
        "namespace App\\Livewire;\n"
        "use Livewire\\Component;\n"
        "class OrdersStatus extends Component {\n"
        "    public string $status = '';\n"
        "    protected array $rules = ['status' => 'required|string', 'order.status' => 'required|string'];\n"
        "    public array $order = [];\n"
        "    public function saveOrder() {}\n"
        "    public function render() {}\n"
        "}\n",
        encoding="utf-8",
    )
    (workspace / "app" / "View" / "Components" / "Orders" / "Card.php").write_text(
        "<?php\n"
        "namespace App\\View\\Components\\Orders;\n"
        "use Illuminate\\View\\Component;\n"
        "class Card extends Component {\n"
        "    public function __construct(public \\App\\Models\\Order $order) {}\n"
        "    public function render() { return view('components.orders.card'); }\n"
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
    (workspace / "app" / "Mail" / "OrderReceiptMail.php").write_text(
        "<?php\n"
        "namespace App\\Mail;\n"
        "class OrderReceiptMail {\n"
        "    public function build() {}\n"
        "}\n",
        encoding="utf-8",
    )
    (workspace / "app" / "Notifications" / "OrderShippedNotification.php").write_text(
        "<?php\n"
        "namespace App\\Notifications;\n"
        "class OrderShippedNotification {\n"
        "    public function toMail($notifiable) {}\n"
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
        "use Illuminate\\Database\\Eloquent\\Casts\\Attribute;\n"
        "use Illuminate\\Database\\Eloquent\\Model;\n"
        "class Order extends Model {\n"
        "    protected $table = 'orders';\n"
        "    protected $fillable = ['customer_id', 'status'];\n"
        "    protected $guarded = ['internal_note'];\n"
        "    protected $hidden = ['internal_note'];\n"
        "    protected $visible = ['id', 'status', 'display_status'];\n"
        "    protected $appends = ['display_status'];\n"
        "    protected $casts = ['status' => 'string'];\n"
        "    public function casts(): array {\n"
        "        return ['customer_id' => 'integer'];\n"
        "    }\n"
        "    public function getDisplayStatusAttribute($value) {\n"
        "        return strtoupper($value);\n"
        "    }\n"
        "    protected function normalizedStatus(): Attribute {\n"
        "        return Attribute::make(\n"
        "            get: fn ($value) => trim($value),\n"
        "            set: fn ($value) => strtolower($value),\n"
        "        );\n"
        "    }\n"
        "    public function customer() {\n"
        "        return $this->belongsTo(Customer::class);\n"
        "    }\n"
        "    public function scopeRecent($query) {\n"
        "        return $query->latest();\n"
        "    }\n"
        "    use \\Illuminate\\Database\\Eloquent\\SoftDeletes;\n"
        "}\n",
        encoding="utf-8",
    )
    (workspace / "app" / "Services" / "OrderService.php").write_text(
        "<?php\n"
        "namespace App\\Services;\n"
        "use App\\Exceptions\\OrderLockedException;\n"
        "class OrderService {\n"
        "    public static function format($order) { throw new OrderLockedException(); }\n"
        "}\n",
        encoding="utf-8",
    )
    (workspace / "app" / "Exceptions" / "OrderLockedException.php").write_text(
        "<?php\n"
        "namespace App\\Exceptions;\n"
        "class OrderLockedException extends \\RuntimeException {}\n",
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
        "use App\\Policies\\OrderPolicy;\n"
        "use App\\Services\\OrderService;\n"
        "use Illuminate\\Support\\Facades\\Blade;\n"
        "use Illuminate\\Support\\Facades\\Gate;\n"
        "class AuthServiceProvider {\n"
        "    protected array $policies = [Order::class => OrderPolicy::class];\n"
        "    protected $listen = [OrderPlaced::class => [SendOrderReceipt::class]];\n"
        "    public function register() {\n"
        "        $this->app->singleton(OrderFormatter::class, OrderService::class);\n"
        "    }\n"
        "    public function boot() {\n"
        "        Order::observe(OrderObserver::class);\n"
        "        Blade::component(\\App\\View\\Components\\Orders\\Card::class, 'orders-card');\n"
        "        Gate::policy(\\App\\Models\\Invoice::class, \\App\\Policies\\InvoicePolicy::class);\n"
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
        "@include('orders.partials.summary', ['order' => $order])\n"
        "<x-alert type=\"info\" />\n"
        "@livewire('orders-status')\n"
        "<form action=\"{{ route('invoices.update', ['invoice' => 7]) }}\" method=\"POST\">\n"
        "@csrf\n"
        "@method('PUT')\n"
        "</form>\n"
        "<form action=\"{{ route('invoices.store') }}\" method=\"POST\">\n"
        "@csrf\n"
        "</form>\n"
        "<a href=\"{{ route('orders.show') }}\">Broken order link</a>\n"
        "@can('view', $order)\n"
        "<span>Allowed</span>\n"
        "@endcan\n"
        "@canany(['update', 'delete'], $order)\n"
        "<span>Bulk allowed</span>\n"
        "@endcanany\n"
        "<input type=\"text\" name=\"customer_id\" value=\"{{ old('customer_id') }}\">\n"
        "@error('customer_id')\n"
        "<span>{{ $message }}</span>\n"
        "@enderror\n"
        "<input type=\"text\" wire:model.defer=\"status\">\n"
        "<button wire:click.debounce=\"saveOrder\">Save</button>\n"
        "<input type=\"text\" wire:model.lazy=\"order.status\">\n"
        "<input type=\"text\" x-data=\"{ filters: { status: '' }, open: false }\" x-model.debounce=\"filters.status\">\n"
        "<button @click.prevent=\"applyFilters\">Apply</button>\n"
        "<div x-data=\"filtersForm()\"></div>\n"
        "@endsection\n",
        encoding="utf-8",
    )
    (workspace / "resources" / "views" / "orders" / "partials" / "summary.blade.php").write_text(
        "<x-orders.card :order=\"$order\" /><x-orders-card :order=\"$order\" />\n"
        "@can('view', $order)\n"
        "<span>Partial allowed</span>\n"
        "@endcan\n",
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
        "<article>{{ $order->status }} {{ $order->display_status }}</article>\n",
        encoding="utf-8",
    )

    result = execute_job(
        {
            "job_id": "job_php_graph",
            "capability": "populate_backend_ast",
            "payload": {"max_files": 60, "max_symbols": 110, "max_edges": 430},
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
    assert artifact["middleware"]["alias_count"] == 3
    assert artifact["middleware"]["group_count"] == 1
    assert ("GET", "/orders/{order}", "OrderController@show") in routes
    assert ("GET", "/invoices", "InvoiceController@index") in routes
    assert ("POST", "/invoices", "InvoiceController@store") in routes
    assert ("GET", "/invoices/{invoice}", "InvoiceController@show") in routes
    assert ("PUT", "/invoices/{invoice}", "InvoiceController@update") in routes
    assert ("DELETE", "/invoices/{invoice}", "InvoiceController@destroy") in routes
    assert ("class", "App\\Http\\Controllers\\OrderController") in symbols
    assert ("class", "App\\Http\\Controllers\\InvoiceController") in symbols
    assert ("class", "App\\Models\\Order") in symbols
    assert ("class", "App\\Policies\\OrderPolicy") in symbols
    assert ("class", "App\\Services\\OrderService") in symbols
    assert ("class", "App\\Exceptions\\OrderLockedException") in symbols
    assert ("class", "App\\Http\\Requests\\StoreOrderRequest") in symbols
    assert ("class", "App\\Http\\Resources\\OrderResource") in symbols
    assert ("class", "App\\Http\\Middleware\\Authenticate") in symbols
    assert ("class", "App\\Http\\Middleware\\EncryptCookies") in symbols
    assert ("class", "App\\Http\\Middleware\\EnsureEmailIsVerified") in symbols
    assert ("class", "App\\Http\\Middleware\\ThrottleRequests") in symbols
    assert ("middleware_alias", "middleware:auth") in symbols
    assert ("middleware_alias", "middleware:verified") in symbols
    assert ("middleware_alias", "middleware:throttle") in symbols
    assert ("middleware_group", "middleware_group:web") in symbols
    assert ("class", "App\\Jobs\\SyncOrderJob") in symbols
    assert ("class", "App\\Events\\OrderPlaced") in symbols
    assert ("class", "App\\Listeners\\SendOrderReceipt") in symbols
    assert ("class", "App\\Mail\\OrderReceiptMail") in symbols
    assert ("class", "App\\Notifications\\OrderShippedNotification") in symbols
    assert ("class", "App\\Console\\Commands\\SyncOrdersCommand") in symbols
    assert ("interface", "App\\Contracts\\OrderFormatter") in symbols
    assert ("class", "App\\Observers\\OrderObserver") in symbols
    assert ("method", "OrderObserver@updated") in symbols
    assert ("class", "App\\Broadcasting\\OrderChannel") in symbols
    assert ("blade_view", "view:orders.show") in symbols
    assert ("blade_view", "view:orders.partials.summary") in symbols
    assert ("blade_view", "view:layouts.app") in symbols
    assert ("blade_component", "component:alert") in symbols
    assert ("blade_component", "component:orders.card") in symbols
    assert ("class", "App\\Livewire\\OrdersStatus") in symbols
    assert ("class", "App\\View\\Components\\Orders\\Card") in symbols
    assert ("method", "OrdersStatus@saveOrder") in symbols
    assert ("method", "Card@render") in symbols
    assert any(
        symbol["kind"] == "class"
        and symbol["name"] == "App\\View\\Components\\Orders\\Card"
        and symbol["role"] == "view_component"
        for symbol in artifact["symbols"]
    )
    assert ("table", "table:orders") in symbols
    assert ("method", "OrderController@__construct") in symbols
    assert ("method", "OrderController@show") in symbols
    assert ("method", "InvoiceController@index") in symbols
    assert ("method", "Authenticate@handle") in symbols
    assert ("method", "EncryptCookies@handle") in symbols
    assert ("method", "EnsureEmailIsVerified@handle") in symbols
    assert ("method", "Order@customer") in symbols
    assert ("method", "SyncOrderJob@handle") in symbols
    assert ("method", "SendOrderReceipt@handle") in symbols
    assert ("method", "OrderReceiptMail@build") in symbols
    assert ("method", "OrderShippedNotification@toMail") in symbols
    assert ("method", "SyncOrdersCommand@handle") in symbols
    assert ("method", "OrderPolicy@view") in symbols
    assert ("method", "OrderService@format") in symbols
    assert ("method", "OrderResource@toArray") in symbols
    assert ("method", "StoreOrderRequest@authorize") in symbols
    assert ("method", "StoreOrderRequest@prepareForValidation") in symbols
    assert ("route_handler", "route:orders.show", "OrderController@show") in edges
    assert ("route_handler", "route:invoices.index", "InvoiceController@index") in edges
    assert ("route_handler", "route:invoices.show", "InvoiceController@show") in edges
    assert ("route_handler", "route:invoices.update", "InvoiceController@update") in edges
    assert ("route_middleware", "route:orders.show", "middleware:web") in edges
    assert ("route_middleware", "route:orders.show", "middleware:auth") in edges
    assert ("route_middleware", "route:orders.show", "middleware:verified") in edges
    assert ("route_middleware", "route:orders.show", "middleware:throttle:60,1") in edges
    assert ("route_middleware_group", "route:orders.show", "middleware_group:web") in edges
    assert ("route_middleware_class", "route:orders.show", "App\\Http\\Middleware\\Authenticate") in edges
    assert ("route_middleware", "route:invoices.index", "middleware:auth") in edges
    assert ("route_middleware_class", "route:invoices.index", "App\\Http\\Middleware\\Authenticate") in edges
    assert ("route_middleware_class", "route:orders.show", "App\\Http\\Middleware\\EncryptCookies") in edges
    assert ("route_middleware_class", "route:orders.show", "App\\Http\\Middleware\\EnsureEmailIsVerified") in edges
    assert ("route_middleware_class", "route:orders.show", "App\\Http\\Middleware\\ThrottleRequests") in edges
    assert ("route_middleware_method", "route:orders.show", "Authenticate@handle") in edges
    assert ("route_middleware_method", "route:orders.show", "EncryptCookies@handle") in edges
    assert ("route_middleware_method", "route:orders.show", "EnsureEmailIsVerified@handle") in edges
    assert ("route_middleware_method", "route:orders.show", "ThrottleRequests@handle") in edges
    assert ("middleware_alias_class", "middleware:auth", "App\\Http\\Middleware\\Authenticate") in edges
    assert ("middleware_alias_class", "middleware:verified", "App\\Http\\Middleware\\EnsureEmailIsVerified") in edges
    assert ("middleware_alias_class", "middleware:throttle", "App\\Http\\Middleware\\ThrottleRequests") in edges
    assert ("middleware_group_member", "middleware_group:web", "App\\Http\\Middleware\\EncryptCookies") in edges
    assert ("middleware_group_member", "middleware_group:web", "App\\Http\\Middleware\\Authenticate") in edges
    assert {
        "kind": "route_middleware_method",
        "from": "route:orders.show",
        "to": "Authenticate@handle",
        "middleware": "auth",
        "middleware_class": "App\\Http\\Middleware\\Authenticate",
        "method": "GET",
        "uri": "/orders/{order}",
        "path": "routes/web.php",
        "line": 4,
    } in artifact["edges"]
    assert {
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
    } in artifact["edges"]
    assert ("eloquent_relation", "App\\Models\\Order", "App\\Models\\Customer") in edges
    assert ("static_call", "App\\Http\\Controllers\\OrderController", "App\\Services\\OrderService::format") in edges
    assert ("static_call", "OrderController@show", "App\\Services\\OrderService::format") in edges
    assert ("calls_method", "App\\Http\\Controllers\\OrderController", "OrderService@format") in edges
    assert ("calls_method", "OrderController@show", "OrderService@format") in edges
    assert ("uses_form_request", "OrderController@show", "App\\Http\\Requests\\StoreOrderRequest") in edges
    assert ("uses_dependency", "OrderController@__construct", "App\\Services\\OrderService") in edges
    assert ("uses_dependency", "OrderController@show", "App\\Contracts\\OrderFormatter") in edges
    assert ("uses_dependency", "OrderController@show", "App\\Models\\Order") in edges
    assert ("route_model_binding", "route:orders.show", "App\\Models\\Order") in edges
    assert ("route_model_table", "route:orders.show", "table:orders") in edges
    assert ("route_uses_form_request", "route:orders.show", "App\\Http\\Requests\\StoreOrderRequest") in edges
    assert ("request_authorization", "App\\Http\\Requests\\StoreOrderRequest", "authorization:form_request") in edges
    assert ("route_request_authorization", "route:orders.show", "App\\Http\\Requests\\StoreOrderRequest") in edges
    assert ("request_input_mutation", "App\\Http\\Requests\\StoreOrderRequest", "request_field:status") in edges
    assert ("route_request_input_mutation", "route:orders.show", "request_field:status") in edges
    assert ("route_request_validation", "route:orders.show", "validation:customer_id") in edges
    assert ("route_request_validation", "route:orders.show", "validation:status") in edges
    assert ("route_validation_database_rule", "route:orders.show", "table:customers.id") in edges
    assert ("authorization_check", "OrderController@show", "ability:view") in edges
    assert ("authorization_model", "OrderController@show", "App\\Models\\Order") in edges
    assert ("authorization_table", "OrderController@show", "table:orders") in edges
    assert ("route_authorization", "route:orders.show", "ability:view") in edges
    assert ("route_authorization_model", "route:orders.show", "App\\Models\\Order") in edges
    assert ("route_authorization_table", "route:orders.show", "table:orders") in edges
    assert ("authorization_policy_method", "OrderController@show", "OrderPolicy@view") in edges
    assert ("route_authorization_policy_method", "route:orders.show", "OrderPolicy@view") in edges
    assert ("http_abort", "OrderController@show", "http_status:403") in edges
    assert ("route_http_abort", "route:orders.show", "http_status:403") in edges
    assert ("http_response_status", "InvoiceController@update", "http_status:409") in edges
    assert ("route_http_response_status", "route:invoices.update", "http_status:409") in edges
    assert ("http_redirect", "OrderController@show", "redirect_route:orders.index") in edges
    assert ("route_http_redirect", "route:orders.show", "redirect_route:orders.index") in edges
    assert ("cache_access", "OrderController@show", "cache_key:orders.summary") in edges
    assert ("route_cache_access", "route:orders.show", "cache_key:orders.summary") in edges
    assert ("session_access", "OrderController@show", "session_key:orders.notice") in edges
    assert ("route_session_access", "route:orders.show", "session_key:orders.notice") in edges
    assert ("outbound_http_call", "OrderController@show", "http_endpoint:api.example.test/orders/sync") in edges
    assert ("route_outbound_http_call", "route:orders.show", "http_endpoint:api.example.test/orders/sync") in edges
    assert ("storage_access", "OrderController@show", "storage_path:s3:orders/export.csv") in edges
    assert ("route_storage_access", "route:orders.show", "storage_path:s3:orders/export.csv") in edges
    assert ("request_input_access", "OrderController@show", "request_field:customer_note") in edges
    assert ("route_request_input_access", "route:orders.show", "request_field:customer_note") in edges
    assert ("request_file_access", "OrderController@show", "request_file:invoice_pdf") in edges
    assert ("route_request_file_access", "route:orders.show", "request_file:invoice_pdf") in edges
    assert ("cookie_access", "OrderController@show", "cookie:orders_filter") in edges
    assert ("route_cookie_access", "route:orders.show", "cookie:orders_filter") in edges
    assert ("db_transaction", "OrderController@show", "db_transaction:transaction") in edges
    assert ("route_db_transaction", "route:orders.show", "db_transaction:transaction") in edges
    assert ("throws_exception", "OrderService@format", "App\\Exceptions\\OrderLockedException") in edges
    assert {
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
    } in artifact["edges"]
    assert {
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
    } in artifact["edges"]
    assert {
        "kind": "route_authorization_policy_method",
        "from": "route:orders.show",
        "to": "OrderPolicy@view",
        "policy_class": "App\\Policies\\OrderPolicy",
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
    } in artifact["edges"]
    assert {
        "kind": "http_abort",
        "from": "OrderController@show",
        "to": "http_status:403",
        "status_code": 403,
        "abort_helper": "abort_if",
        "path": "app/Http/Controllers/OrderController.php",
        "line": 29,
    } in artifact["edges"]
    assert {
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
        "source_line": 29,
    } in artifact["edges"]
    assert {
        "kind": "http_redirect",
        "from": "OrderController@show",
        "to": "redirect_route:orders.index",
        "redirect_type": "route",
        "redirect_target": "orders.index",
        "redirect_helper": "redirect_route",
        "redirect_status": 302,
        "path": "app/Http/Controllers/OrderController.php",
        "line": 30,
    } in artifact["edges"]
    assert {
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
        "source_line": 30,
    } in artifact["edges"]
    assert {
        "kind": "cache_access",
        "from": "OrderController@show",
        "to": "cache_key:orders.summary",
        "cache_key": "orders.summary",
        "cache_operation": "read_write",
        "cache_method": "cache_remember",
        "cache_ttl_present": True,
        "path": "app/Http/Controllers/OrderController.php",
        "line": 31,
    } in artifact["edges"]
    assert {
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
        "source_line": 31,
    } in artifact["edges"]
    assert {
        "kind": "session_access",
        "from": "OrderController@show",
        "to": "session_key:orders.notice",
        "session_key": "orders.notice",
        "session_operation": "flash",
        "session_method": "session_flash",
        "path": "app/Http/Controllers/OrderController.php",
        "line": 32,
    } in artifact["edges"]
    assert {
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
        "source_line": 32,
    } in artifact["edges"]
    assert {
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
        "line": 33,
    } in artifact["edges"]
    assert {
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
        "source_line": 33,
    } in artifact["edges"]
    assert {
        "kind": "storage_access",
        "from": "OrderController@show",
        "to": "storage_path:s3:orders/export.csv",
        "storage_disk": "s3",
        "storage_path": "orders/export.csv",
        "storage_operation": "write",
        "storage_method": "storage_put",
        "path": "app/Http/Controllers/OrderController.php",
        "line": 34,
    } in artifact["edges"]
    assert {
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
        "source_line": 34,
    } in artifact["edges"]
    assert {
        "kind": "request_input_access",
        "from": "OrderController@show",
        "to": "request_field:customer_note",
        "field": "customer_note",
        "input_source": "input",
        "input_method": "request_input",
        "path": "app/Http/Controllers/OrderController.php",
        "line": 35,
    } in artifact["edges"]
    assert {
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
        "source_line": 35,
    } in artifact["edges"]
    assert {
        "kind": "request_file_access",
        "from": "OrderController@show",
        "to": "request_file:invoice_pdf",
        "file_field": "invoice_pdf",
        "file_operation": "check",
        "file_method": "request_hasfile",
        "path": "app/Http/Controllers/OrderController.php",
        "line": 36,
    } in artifact["edges"]
    assert {
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
        "source_line": 36,
    } in artifact["edges"]
    assert {
        "kind": "cookie_access",
        "from": "OrderController@show",
        "to": "cookie:orders_filter",
        "cookie_name": "orders_filter",
        "cookie_operation": "set",
        "cookie_method": "cookie_queue",
        "path": "app/Http/Controllers/OrderController.php",
        "line": 37,
    } in artifact["edges"]
    assert {
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
        "source_line": 37,
    } in artifact["edges"]
    assert {
        "kind": "db_transaction",
        "from": "OrderController@show",
        "to": "db_transaction:transaction",
        "transaction_operation": "transaction",
        "transaction_method": "DB::transaction",
        "path": "app/Http/Controllers/OrderController.php",
        "line": 38,
    } in artifact["edges"]
    assert {
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
        "source_line": 38,
    } in artifact["edges"]
    assert {
        "kind": "calls_method",
        "from": "OrderController@show",
        "to": "OrderService@format",
        "class_context": "App\\Http\\Controllers\\OrderController",
        "target_class": "App\\Services\\OrderService",
        "call_type": "static",
        "target_method": "format",
        "path": "app/Http/Controllers/OrderController.php",
        "line": 26,
    } in artifact["edges"]
    assert {
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
        "line": 27,
    } in artifact["edges"]
    assert {
        "kind": "calls_method",
        "from": "OrderController@show",
        "to": "OrderService@format",
        "target_class": "App\\Services\\OrderService",
        "call_type": "property",
        "receiver": "orders",
        "target_method": "format",
        "path": "app/Http/Controllers/OrderController.php",
        "line": 28,
    } in artifact["edges"]
    assert {
        "kind": "throws_exception",
        "from": "OrderService@format",
        "to": "App\\Exceptions\\OrderLockedException",
        "exception_class": "App\\Exceptions\\OrderLockedException",
        "exception_short_name": "OrderLockedException",
        "path": "app/Services/OrderService.php",
        "line": 5,
    } in artifact["edges"]
    assert {
        "kind": "http_response_status",
        "from": "InvoiceController@update",
        "to": "http_status:409",
        "status_code": 409,
        "response_helper": "response_json",
        "path": "app/Http/Controllers/InvoiceController.php",
        "line": 7,
    } in artifact["edges"]
    assert {
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
    } in artifact["edges"]
    assert {
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
    } in artifact["edges"]
    assert {
        "kind": "route_validation_database_rule",
        "from": "route:orders.show",
        "to": "table:customers.id",
        "field": "customer_id",
        "rule": "exists",
        "table": "customers",
        "request_class": "App\\Http\\Requests\\StoreOrderRequest",
        "validation_path": "app/Http/Requests/StoreOrderRequest.php",
        "validation_line": 6,
        "handler": "OrderController@show",
        "param": "request",
        "method": "GET",
        "uri": "/orders/{order}",
        "path": "routes/web.php",
        "line": 4,
        "column": "id",
    } in artifact["edges"]
    assert {
        "kind": "request_authorization",
        "from": "App\\Http\\Requests\\StoreOrderRequest",
        "to": "authorization:form_request",
        "authorization_result": "deny",
        "authorization_path": "app/Http/Requests/StoreOrderRequest.php",
        "authorization_line": 8,
        "path": "app/Http/Requests/StoreOrderRequest.php",
        "line": 8,
    } in artifact["edges"]
    assert {
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
    } in artifact["edges"]
    assert {
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
    } in artifact["edges"]
    assert {
        "kind": "route_request_input_mutation",
        "from": "route:orders.show",
        "to": "request_field:customer_id",
        "request_class": "App\\Http\\Requests\\StoreOrderRequest",
        "handler": "OrderController@show",
        "param": "request",
        "field": "customer_id",
        "operation": "merge",
        "mutation_stage": "prepare_for_validation",
        "mutation_path": "app/Http/Requests/StoreOrderRequest.php",
        "mutation_line": 10,
        "method": "GET",
        "uri": "/orders/{order}",
        "path": "routes/web.php",
        "line": 4,
    } in artifact["edges"]
    assert ("request_validation", "App\\Http\\Requests\\StoreOrderRequest", "validation:customer_id") in edges
    assert ("request_validation", "App\\Http\\Controllers\\OrderController", "validation:status") in edges
    assert ("request_validation", "OrderController@show", "validation:status") in edges
    assert ("validation_database_rule", "App\\Http\\Requests\\StoreOrderRequest", "table:customers.id") in edges
    assert {
        "kind": "request_validation",
        "from": "App\\Http\\Requests\\StoreOrderRequest",
        "to": "validation:customer_id",
        "validation_rules": ["required", "integer", "exists"],
        "path": "app/Http/Requests/StoreOrderRequest.php",
        "line": 6,
    } in artifact["edges"]
    assert {
        "kind": "validation_database_rule",
        "from": "App\\Http\\Requests\\StoreOrderRequest",
        "to": "table:customers.id",
        "field": "customer_id",
        "rule": "exists",
        "table": "customers",
        "path": "app/Http/Requests/StoreOrderRequest.php",
        "line": 6,
        "column": "id",
    } in artifact["edges"]
    assert ("dispatches_job", "App\\Http\\Controllers\\OrderController", "App\\Jobs\\SyncOrderJob") in edges
    assert ("dispatches_job", "OrderController@show", "App\\Jobs\\SyncOrderJob") in edges
    assert ("dispatches_job_method", "OrderController@show", "SyncOrderJob@handle") in edges
    assert ("route_dispatches_job_method", "route:orders.show", "SyncOrderJob@handle") in edges
    assert {
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
        "source_line": 18,
    } in artifact["edges"]
    assert ("emits_event", "App\\Http\\Controllers\\OrderController", "App\\Events\\OrderPlaced") in edges
    assert ("emits_event", "OrderController@show", "App\\Events\\OrderPlaced") in edges
    assert ("event_listener", "App\\Events\\OrderPlaced", "App\\Listeners\\SendOrderReceipt") in edges
    assert ("event_listener_method", "App\\Events\\OrderPlaced", "SendOrderReceipt@handle") in edges
    assert ("emits_event_listener", "OrderController@show", "SendOrderReceipt@handle") in edges
    assert ("route_emits_event_listener", "route:orders.show", "SendOrderReceipt@handle") in edges
    assert ("sends_mail", "OrderController@show", "App\\Mail\\OrderReceiptMail") in edges
    assert ("sends_mail_method", "OrderController@show", "OrderReceiptMail@build") in edges
    assert ("route_sends_mail_method", "route:orders.show", "OrderReceiptMail@build") in edges
    assert (
        "sends_notification",
        "OrderController@show",
        "App\\Notifications\\OrderShippedNotification",
    ) in edges
    assert ("sends_notification_method", "OrderController@show", "OrderShippedNotification@toMail") in edges
    assert ("route_sends_notification_method", "route:orders.show", "OrderShippedNotification@toMail") in edges
    assert {
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
        "source_line": 19,
        "listener_path": "app/Providers/AuthServiceProvider.php",
        "listener_line": 14,
    } in artifact["edges"]
    assert {
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
        "source_line": 19,
    } in artifact["edges"]
    assert {
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
        "source_line": 19,
    } in artifact["edges"]
    assert ("artisan_command", "App\\Console\\Commands\\SyncOrdersCommand", "command:orders:sync") in edges
    assert ("artisan_command_method", "command:orders:sync", "SyncOrdersCommand@handle") in edges
    assert ("scheduled_command", "App\\Console\\Kernel", "command:orders:sync") in edges
    assert ("scheduled_command_method", "App\\Console\\Kernel", "SyncOrdersCommand@handle") in edges
    assert ("scheduled_job", "App\\Console\\Kernel", "App\\Jobs\\SyncOrderJob") in edges
    assert ("scheduled_job_method", "App\\Console\\Kernel", "SyncOrderJob@handle") in edges
    assert {
        "kind": "scheduled_command_method",
        "from": "App\\Console\\Kernel",
        "to": "SyncOrdersCommand@handle",
        "command": "command:orders:sync",
        "command_class": "App\\Console\\Commands\\SyncOrdersCommand",
        "command_method": "handle",
        "cadence": "hourly",
        "path": "app/Console/Kernel.php",
        "line": 6,
    } in artifact["edges"]
    assert {
        "kind": "scheduled_job_method",
        "from": "App\\Console\\Kernel",
        "to": "SyncOrderJob@handle",
        "job_class": "App\\Jobs\\SyncOrderJob",
        "job_method": "handle",
        "cadence": "daily",
        "path": "app/Console/Kernel.php",
        "line": 7,
    } in artifact["edges"]
    assert ("query_table", "App\\Http\\Controllers\\OrderController", "table:orders") in edges
    assert ("query_table", "App\\Http\\Controllers\\OrderController", "table:customers") in edges
    assert ("query_table", "OrderController@show", "table:orders") in edges
    assert ("query_table", "OrderController@show", "table:customers") in edges
    assert ("query_operation", "OrderController@show", "query:customers:join") in edges
    assert ("query_operation", "OrderController@show", "query:orders:first") in edges
    assert ("query_operation", "OrderController@show", "query:orders:update") in edges
    assert ("query_operation", "OrderController@show", "query:orders:withTrashed") in edges
    assert ("query_operation", "OrderController@show", "query:orders:lockForUpdate") in edges
    assert ("query_operation", "OrderController@show", "query:orders:forceDelete") in edges
    assert ("query_operation", "OrderController@show", "query:orders:restore") in edges
    assert ("model_instance_operation", "OrderController@show", "model_operation:orders:restore") in edges
    assert ("route_model_instance_operation", "route:orders.show", "model_operation:orders:restore") in edges
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
        "line": 21,
    } in artifact["edges"]
    assert {
        "kind": "query_read",
        "from": "OrderController@show",
        "to": "table:orders",
        "class_context": "App\\Http\\Controllers\\OrderController",
        "model": "App\\Models\\Order",
        "query_method": "first",
        "path": "app/Http/Controllers/OrderController.php",
        "line": 22,
    } in artifact["edges"]
    assert {
        "kind": "query_operation",
        "from": "OrderController@show",
        "to": "query:orders:update",
        "class_context": "App\\Http\\Controllers\\OrderController",
        "table": "orders",
        "model": "App\\Models\\Order",
        "operation": "update",
        "access": "write",
        "path": "app/Http/Controllers/OrderController.php",
        "line": 23,
    } in artifact["edges"]
    assert {
        "kind": "query_operation",
        "from": "OrderController@show",
        "to": "query:orders:withTrashed",
        "class_context": "App\\Http\\Controllers\\OrderController",
        "table": "orders",
        "model": "App\\Models\\Order",
        "operation": "withTrashed",
        "access": "scope",
        "path": "app/Http/Controllers/OrderController.php",
        "line": 39,
    } in artifact["edges"]
    assert {
        "kind": "query_operation",
        "from": "OrderController@show",
        "to": "query:orders:lockForUpdate",
        "class_context": "App\\Http\\Controllers\\OrderController",
        "table": "orders",
        "model": "App\\Models\\Order",
        "operation": "lockForUpdate",
        "access": "lock",
        "path": "app/Http/Controllers/OrderController.php",
        "line": 40,
    } in artifact["edges"]
    assert {
        "kind": "query_operation",
        "from": "OrderController@show",
        "to": "query:orders:forceDelete",
        "class_context": "App\\Http\\Controllers\\OrderController",
        "table": "orders",
        "model": "App\\Models\\Order",
        "operation": "forceDelete",
        "access": "write",
        "path": "app/Http/Controllers/OrderController.php",
        "line": 41,
    } in artifact["edges"]
    assert {
        "kind": "query_operation",
        "from": "OrderController@show",
        "to": "query:orders:restore",
        "class_context": "App\\Http\\Controllers\\OrderController",
        "table": "orders",
        "model": "App\\Models\\Order",
        "operation": "restore",
        "access": "write",
        "path": "app/Http/Controllers/OrderController.php",
        "line": 42,
    } in artifact["edges"]
    assert {
        "kind": "model_instance_operation",
        "from": "OrderController@show",
        "to": "model_operation:orders:restore",
        "model": "App\\Models\\Order",
        "table": "orders",
        "operation": "restore",
        "access": "restore",
        "receiver": "order",
        "source_path": "app/Http/Controllers/OrderController.php",
        "source_line": 43,
        "path": "app/Http/Controllers/OrderController.php",
        "line": 43,
    } in artifact["edges"]
    assert {
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
    } in artifact["edges"]
    assert ("eloquent_query", "App\\Http\\Controllers\\OrderController", "App\\Models\\Order::where") in edges
    assert ("eloquent_query", "OrderController@show", "App\\Models\\Order::where") in edges
    assert ("view_ref", "App\\Http\\Controllers\\OrderController", "view:orders.show") in edges
    assert ("view_ref", "OrderController@show", "view:orders.show") in edges
    assert ("blade_extends", "view:orders.show", "view:layouts.app") in edges
    assert ("blade_include", "view:orders.show", "view:orders.partials.summary") in edges
    assert ("blade_include_data", "view:orders.show", "view_data:orders.partials.summary.order") in edges
    assert (
        "blade_include_data_route_param",
        "view_data:orders.partials.summary.order",
        "route_param:orders.show.order",
    ) in edges
    assert ("blade_include", "view:layouts.app", "view:shared.flash") in edges
    assert ("blade_include", "view:layouts.app", "view:shared.banner") in edges
    assert ("blade_component", "view:orders.show", "component:alert") in edges
    assert ("blade_component", "view:orders.partials.summary", "component:orders.card") in edges
    assert ("blade_component", "view:orders.partials.summary", "component:orders-card") in edges
    assert ("blade_component_class", "component:orders.card", "App\\View\\Components\\Orders\\Card") in edges
    assert ("blade_component_class", "component:orders-card", "App\\View\\Components\\Orders\\Card") in edges
    assert ("blade_component_render_method", "component:orders.card", "Card@render") in edges
    assert ("blade_component_render_method", "component:orders-card", "Card@render") in edges
    assert (
        "blade_component_template_param",
        "component:orders.card",
        "component_param:App\\View\\Components\\Orders\\Card.order",
    ) in edges
    assert (
        "blade_component_template_model_field",
        "component_param:App\\View\\Components\\Orders\\Card.order",
        "table:orders.status",
    ) in edges
    assert (
        "blade_component_template_model_attribute",
        "component_param:App\\View\\Components\\Orders\\Card.order",
        "model_attribute:App\\Models\\Order.display_status",
    ) in edges
    assert ("blade_component_prop", "view:orders.partials.summary", "component_prop:orders.card.order") in edges
    assert ("blade_component_prop", "view:orders.partials.summary", "component_prop:orders-card.order") in edges
    assert (
        "blade_component_prop_class_param",
        "component_prop:orders.card.order",
        "component_param:App\\View\\Components\\Orders\\Card.order",
    ) in edges
    assert (
        "blade_component_prop_class_param",
        "component_prop:orders-card.order",
        "component_param:App\\View\\Components\\Orders\\Card.order",
    ) in edges
    assert (
        "blade_component_prop_include_data",
        "component_prop:orders.card.order",
        "view_data:orders.partials.summary.order",
    ) in edges
    assert (
        "blade_component_prop_include_route_param",
        "component_prop:orders.card.order",
        "route_param:orders.show.order",
    ) in edges
    assert ("blade_component_prop_include_model", "component_prop:orders.card.order", "App\\Models\\Order") in edges
    assert ("livewire_component", "view:orders.show", "livewire:orders-status") in edges
    assert ("livewire_component_class", "livewire:orders-status", "App\\Livewire\\OrdersStatus") in edges
    assert ("blade_route_ref", "view:orders.show", "route:invoices.update") in edges
    assert ("blade_csrf_token", "view:orders.show", "csrf:present") in edges
    assert ("blade_form_method", "view:orders.show", "http_method:PUT") in edges
    assert ("blade_form_route_method", "view:orders.show", "route:invoices.update") in edges
    assert ("blade_form_route_method", "view:orders.show", "route:invoices.store") in edges
    assert ("blade_route_param", "view:orders.show", "route_param:invoices.update.invoice") in edges
    assert ("blade_route_param", "view:orders.show", "route_param:orders.show.order") in edges
    assert ("blade_authorization", "view:orders.show", "ability:view") in edges
    assert ("blade_authorization", "view:orders.show", "ability:update") in edges
    assert ("blade_authorization", "view:orders.show", "ability:delete") in edges
    assert ("blade_authorization_route_param", "ability:view", "route_param:orders.show.order") in edges
    assert ("blade_authorization_route_param", "ability:update", "route_param:orders.show.order") in edges
    assert ("blade_authorization_route_param", "ability:delete", "route_param:orders.show.order") in edges
    assert ("blade_authorization_model", "ability:view", "App\\Models\\Order") in edges
    assert ("blade_authorization_model", "ability:update", "App\\Models\\Order") in edges
    assert ("blade_authorization_model", "ability:delete", "App\\Models\\Order") in edges
    assert ("blade_authorization_policy_method", "ability:view", "OrderPolicy@view") in edges
    assert ("blade_authorization", "view:orders.partials.summary", "ability:view") in edges
    assert ("blade_authorization_include_data", "ability:view", "view_data:orders.partials.summary.order") in edges
    assert ("blade_authorization_include_route_param", "ability:view", "route_param:orders.show.order") in edges
    assert ("blade_authorization_include_model", "ability:view", "App\\Models\\Order") in edges
    assert ("blade_authorization_include_policy_method", "ability:view", "OrderPolicy@view") in edges
    assert ("blade_form_field", "view:orders.show", "request_field:customer_id") in edges
    assert ("blade_old_input", "view:orders.show", "request_field:customer_id") in edges
    assert ("blade_validation_error", "view:orders.show", "validation:customer_id") in edges
    assert ("blade_wire_model", "view:orders.show", "livewire_property:status") in edges
    assert (
        "blade_wire_model_property",
        "livewire_property:status",
        "livewire_property:App\\Livewire\\OrdersStatus.status",
    ) in edges
    assert ("livewire_validation", "App\\Livewire\\OrdersStatus", "validation:status") in edges
    assert ("blade_wire_model_validation", "livewire_property:status", "validation:status") in edges
    assert ("blade_wire_model", "view:orders.show", "livewire_property:order.status") in edges
    assert (
        "blade_wire_model_property",
        "livewire_property:order.status",
        "livewire_property:App\\Livewire\\OrdersStatus.order",
    ) in edges
    assert ("livewire_validation", "App\\Livewire\\OrdersStatus", "validation:order.status") in edges
    assert ("blade_wire_model_validation", "livewire_property:order.status", "validation:order.status") in edges
    assert ("blade_alpine_data", "view:orders.show", "alpine_state:filters") in edges
    assert ("blade_alpine_data", "view:orders.show", "alpine_state:open") in edges
    assert ("blade_alpine_model", "view:orders.show", "alpine_state:filters.status") in edges
    assert ("blade_alpine_model_data", "alpine_state:filters.status", "alpine_state:filters") in edges
    assert ("blade_alpine_action", "view:orders.show", "alpine_action:applyFilters") in edges
    assert ("blade_alpine_data_factory", "view:orders.show", "alpine_factory:filtersForm") in edges
    assert ("blade_wire_action", "view:orders.show", "livewire_action:saveOrder") in edges
    assert ("blade_wire_action_method", "livewire_action:saveOrder", "OrdersStatus@saveOrder") in edges
    assert {
        "kind": "livewire_component_class",
        "from": "livewire:orders-status",
        "to": "App\\Livewire\\OrdersStatus",
        "livewire_alias": "orders-status",
        "livewire_class": "App\\Livewire\\OrdersStatus",
        "path": "resources/views/orders/show.blade.php",
        "line": 5,
    } in artifact["edges"]
    assert {
        "kind": "blade_route_ref",
        "from": "view:orders.show",
        "to": "route:invoices.update",
        "route_name": "invoices.update",
        "path": "resources/views/orders/show.blade.php",
        "line": 6,
    } in artifact["edges"]
    assert {
        "kind": "blade_include_data",
        "from": "view:orders.show",
        "to": "view_data:orders.partials.summary.order",
        "included_view": "orders.partials.summary",
        "include_data_key": "order",
        "include_source_variable": "order",
        "path": "resources/views/orders/show.blade.php",
        "line": 3,
    } in artifact["edges"]
    assert {
        "kind": "blade_include_data_route_param",
        "from": "view_data:orders.partials.summary.order",
        "to": "route_param:orders.show.order",
        "included_view": "orders.partials.summary",
        "include_data_key": "order",
        "include_source_variable": "order",
        "route_name": "orders.show",
        "route_param": "order",
        "path": "resources/views/orders/show.blade.php",
        "line": 3,
    } in artifact["edges"]
    assert {
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
    } in artifact["edges"]
    assert {
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
    } in artifact["edges"]
    assert {
        "kind": "blade_authorization",
        "from": "view:orders.show",
        "to": "ability:view",
        "ability": "view",
        "authorization_helper": "can",
        "authorization_subject": "order",
        "path": "resources/views/orders/show.blade.php",
        "line": 14,
    } in artifact["edges"]
    assert {
        "kind": "blade_authorization",
        "from": "view:orders.show",
        "to": "ability:update",
        "ability": "update",
        "authorization_helper": "canany",
        "authorization_subject": "order",
        "path": "resources/views/orders/show.blade.php",
        "line": 17,
    } in artifact["edges"]
    assert {
        "kind": "blade_authorization",
        "from": "view:orders.show",
        "to": "ability:delete",
        "ability": "delete",
        "authorization_helper": "canany",
        "authorization_subject": "order",
        "path": "resources/views/orders/show.blade.php",
        "line": 17,
    } in artifact["edges"]
    assert {
        "kind": "blade_authorization_route_param",
        "from": "ability:view",
        "to": "route_param:orders.show.order",
        "ability": "view",
        "authorization_helper": "can",
        "authorization_subject": "order",
        "route_name": "orders.show",
        "route_param": "order",
        "path": "resources/views/orders/show.blade.php",
        "line": 14,
    } in artifact["edges"]
    assert {
        "kind": "blade_authorization_route_param",
        "from": "ability:update",
        "to": "route_param:orders.show.order",
        "ability": "update",
        "authorization_helper": "canany",
        "authorization_subject": "order",
        "route_name": "orders.show",
        "route_param": "order",
        "path": "resources/views/orders/show.blade.php",
        "line": 17,
    } in artifact["edges"]
    assert {
        "kind": "blade_authorization_route_param",
        "from": "ability:delete",
        "to": "route_param:orders.show.order",
        "ability": "delete",
        "authorization_helper": "canany",
        "authorization_subject": "order",
        "route_name": "orders.show",
        "route_param": "order",
        "path": "resources/views/orders/show.blade.php",
        "line": 17,
    } in artifact["edges"]
    assert {
        "kind": "blade_authorization_model",
        "from": "ability:view",
        "to": "App\\Models\\Order",
        "ability": "view",
        "authorization_helper": "can",
        "authorization_subject": "order",
        "route_name": "orders.show",
        "route_param": "order",
        "model": "App\\Models\\Order",
        "table": "orders",
        "path": "resources/views/orders/show.blade.php",
        "line": 14,
    } in artifact["edges"]
    assert {
        "kind": "blade_authorization_policy_method",
        "from": "ability:view",
        "to": "OrderPolicy@view",
        "ability": "view",
        "authorization_helper": "can",
        "authorization_subject": "order",
        "route_name": "orders.show",
        "route_param": "order",
        "policy_class": "App\\Policies\\OrderPolicy",
        "model": "App\\Models\\Order",
        "table": "orders",
        "path": "resources/views/orders/show.blade.php",
        "line": 14,
    } in artifact["edges"]
    assert {
        "kind": "blade_component_class",
        "from": "component:orders.card",
        "to": "App\\View\\Components\\Orders\\Card",
        "component": "orders.card",
        "component_class": "App\\View\\Components\\Orders\\Card",
        "component_path": "app/View/Components/Orders/Card.php",
        "component_line": 4,
        "path": "resources/views/orders/partials/summary.blade.php",
        "line": 1,
    } in artifact["edges"]
    assert {
        "kind": "blade_component_render_method",
        "from": "component:orders.card",
        "to": "Card@render",
        "component": "orders.card",
        "component_class": "App\\View\\Components\\Orders\\Card",
        "component_method": "render",
        "path": "resources/views/orders/partials/summary.blade.php",
        "line": 1,
    } in artifact["edges"]
    assert {
        "kind": "blade_component_class",
        "from": "component:orders-card",
        "to": "App\\View\\Components\\Orders\\Card",
        "component": "orders-card",
        "component_class": "App\\View\\Components\\Orders\\Card",
        "component_alias_source": "blade_component_registration",
        "component_registration_path": "app/Providers/AuthServiceProvider.php",
        "component_registration_line": 20,
        "component_path": "app/View/Components/Orders/Card.php",
        "component_line": 4,
        "path": "resources/views/orders/partials/summary.blade.php",
        "line": 1,
    } in artifact["edges"]
    assert {
        "kind": "blade_component_render_method",
        "from": "component:orders-card",
        "to": "Card@render",
        "component": "orders-card",
        "component_class": "App\\View\\Components\\Orders\\Card",
        "component_method": "render",
        "path": "resources/views/orders/partials/summary.blade.php",
        "line": 1,
    } in artifact["edges"]
    assert {
        "kind": "blade_component_template_param",
        "from": "component:orders.card",
        "to": "component_param:App\\View\\Components\\Orders\\Card.order",
        "component": "orders.card",
        "component_class": "App\\View\\Components\\Orders\\Card",
        "component_param": "order",
        "component_param_type": "App\\Models\\Order",
        "template_variable": "order",
        "component_path": "app/View/Components/Orders/Card.php",
        "component_line": 5,
        "model": "App\\Models\\Order",
        "table": "orders",
        "path": "resources/views/components/orders/card.blade.php",
        "line": 1,
    } in artifact["edges"]
    assert {
        "kind": "blade_component_template_model_field",
        "from": "component_param:App\\View\\Components\\Orders\\Card.order",
        "to": "table:orders.status",
        "component": "orders.card",
        "component_class": "App\\View\\Components\\Orders\\Card",
        "component_param": "order",
        "component_param_type": "App\\Models\\Order",
        "template_variable": "order",
        "template_field": "status",
        "model": "App\\Models\\Order",
        "table": "orders",
        "field": "status",
        "path": "resources/views/components/orders/card.blade.php",
        "line": 1,
    } in artifact["edges"]
    assert {
        "kind": "blade_component_template_model_attribute",
        "from": "component_param:App\\View\\Components\\Orders\\Card.order",
        "to": "model_attribute:App\\Models\\Order.display_status",
        "component": "orders.card",
        "component_class": "App\\View\\Components\\Orders\\Card",
        "component_param": "order",
        "component_param_type": "App\\Models\\Order",
        "template_variable": "order",
        "template_field": "display_status",
        "model": "App\\Models\\Order",
        "table": "orders",
        "field": "display_status",
        "attribute_kind": "model_appended_attribute",
        "attribute_path": "app/Models/Order.php",
        "attribute_line": 11,
        "path": "resources/views/components/orders/card.blade.php",
        "line": 1,
    } in artifact["edges"]
    assert {
        "kind": "blade_component_prop",
        "from": "view:orders.partials.summary",
        "to": "component_prop:orders.card.order",
        "component": "orders.card",
        "component_prop": "order",
        "component_source_variable": "order",
        "path": "resources/views/orders/partials/summary.blade.php",
        "line": 1,
    } in artifact["edges"]
    assert {
        "kind": "blade_component_prop_class_param",
        "from": "component_prop:orders.card.order",
        "to": "component_param:App\\View\\Components\\Orders\\Card.order",
        "component": "orders.card",
        "component_prop": "order",
        "component_source_variable": "order",
        "component_class": "App\\View\\Components\\Orders\\Card",
        "component_param": "order",
        "component_param_type": "App\\Models\\Order",
        "component_path": "app/View/Components/Orders/Card.php",
        "component_line": 5,
        "model": "App\\Models\\Order",
        "table": "orders",
        "path": "resources/views/orders/partials/summary.blade.php",
        "line": 1,
    } in artifact["edges"]
    assert {
        "kind": "blade_component_prop_class_param",
        "from": "component_prop:orders-card.order",
        "to": "component_param:App\\View\\Components\\Orders\\Card.order",
        "component": "orders-card",
        "component_prop": "order",
        "component_source_variable": "order",
        "component_class": "App\\View\\Components\\Orders\\Card",
        "component_alias_source": "blade_component_registration",
        "component_registration_path": "app/Providers/AuthServiceProvider.php",
        "component_registration_line": 20,
        "component_param": "order",
        "component_param_type": "App\\Models\\Order",
        "component_path": "app/View/Components/Orders/Card.php",
        "component_line": 5,
        "model": "App\\Models\\Order",
        "table": "orders",
        "path": "resources/views/orders/partials/summary.blade.php",
        "line": 1,
    } in artifact["edges"]
    assert {
        "kind": "blade_component_prop_include_data",
        "from": "component_prop:orders.card.order",
        "to": "view_data:orders.partials.summary.order",
        "component": "orders.card",
        "component_prop": "order",
        "component_source_variable": "order",
        "included_view": "orders.partials.summary",
        "include_data_key": "order",
        "include_source_variable": "order",
        "include_parent_view": "view:orders.show",
        "path": "resources/views/orders/partials/summary.blade.php",
        "line": 1,
    } in artifact["edges"]
    assert {
        "kind": "blade_component_prop_include_route_param",
        "from": "component_prop:orders.card.order",
        "to": "route_param:orders.show.order",
        "component": "orders.card",
        "component_prop": "order",
        "component_source_variable": "order",
        "included_view": "orders.partials.summary",
        "include_data_key": "order",
        "include_source_variable": "order",
        "include_parent_view": "view:orders.show",
        "route_name": "orders.show",
        "route_param": "order",
        "path": "resources/views/orders/partials/summary.blade.php",
        "line": 1,
    } in artifact["edges"]
    assert {
        "kind": "blade_component_prop_include_model",
        "from": "component_prop:orders.card.order",
        "to": "App\\Models\\Order",
        "component": "orders.card",
        "component_prop": "order",
        "component_source_variable": "order",
        "included_view": "orders.partials.summary",
        "include_data_key": "order",
        "include_source_variable": "order",
        "include_parent_view": "view:orders.show",
        "route_name": "orders.show",
        "route_param": "order",
        "model": "App\\Models\\Order",
        "table": "orders",
        "path": "resources/views/orders/partials/summary.blade.php",
        "line": 1,
    } in artifact["edges"]
    assert {
        "kind": "blade_authorization",
        "from": "view:orders.partials.summary",
        "to": "ability:view",
        "ability": "view",
        "authorization_helper": "can",
        "authorization_subject": "order",
        "path": "resources/views/orders/partials/summary.blade.php",
        "line": 2,
    } in artifact["edges"]
    assert {
        "kind": "blade_authorization_include_data",
        "from": "ability:view",
        "to": "view_data:orders.partials.summary.order",
        "ability": "view",
        "authorization_helper": "can",
        "authorization_subject": "order",
        "included_view": "orders.partials.summary",
        "include_data_key": "order",
        "include_source_variable": "order",
        "include_parent_view": "view:orders.show",
        "path": "resources/views/orders/partials/summary.blade.php",
        "line": 2,
    } in artifact["edges"]
    assert {
        "kind": "blade_authorization_include_route_param",
        "from": "ability:view",
        "to": "route_param:orders.show.order",
        "ability": "view",
        "authorization_helper": "can",
        "authorization_subject": "order",
        "included_view": "orders.partials.summary",
        "include_data_key": "order",
        "include_source_variable": "order",
        "include_parent_view": "view:orders.show",
        "route_name": "orders.show",
        "route_param": "order",
        "path": "resources/views/orders/partials/summary.blade.php",
        "line": 2,
    } in artifact["edges"]
    assert {
        "kind": "blade_authorization_include_model",
        "from": "ability:view",
        "to": "App\\Models\\Order",
        "ability": "view",
        "authorization_helper": "can",
        "authorization_subject": "order",
        "included_view": "orders.partials.summary",
        "include_data_key": "order",
        "include_source_variable": "order",
        "include_parent_view": "view:orders.show",
        "route_name": "orders.show",
        "route_param": "order",
        "model": "App\\Models\\Order",
        "table": "orders",
        "path": "resources/views/orders/partials/summary.blade.php",
        "line": 2,
    } in artifact["edges"]
    assert {
        "kind": "blade_authorization_include_policy_method",
        "from": "ability:view",
        "to": "OrderPolicy@view",
        "ability": "view",
        "authorization_helper": "can",
        "authorization_subject": "order",
        "included_view": "orders.partials.summary",
        "include_data_key": "order",
        "include_source_variable": "order",
        "include_parent_view": "view:orders.show",
        "route_name": "orders.show",
        "route_param": "order",
        "policy_class": "App\\Policies\\OrderPolicy",
        "model": "App\\Models\\Order",
        "table": "orders",
        "path": "resources/views/orders/partials/summary.blade.php",
        "line": 2,
    } in artifact["edges"]
    assert {
        "kind": "blade_form_field",
        "from": "view:orders.show",
        "to": "request_field:customer_id",
        "form_field": "customer_id",
        "form_field_tag": "input",
        "path": "resources/views/orders/show.blade.php",
        "line": 20,
    } in artifact["edges"]
    assert {
        "kind": "blade_old_input",
        "from": "view:orders.show",
        "to": "request_field:customer_id",
        "form_field": "customer_id",
        "input_helper": "old",
        "path": "resources/views/orders/show.blade.php",
        "line": 20,
    } in artifact["edges"]
    assert {
        "kind": "blade_validation_error",
        "from": "view:orders.show",
        "to": "validation:customer_id",
        "form_field": "customer_id",
        "validation_helper": "error",
        "path": "resources/views/orders/show.blade.php",
        "line": 21,
    } in artifact["edges"]
    assert {
        "kind": "blade_wire_model",
        "from": "view:orders.show",
        "to": "livewire_property:status",
        "wire_model": "status",
        "wire_modifiers": ["defer"],
        "path": "resources/views/orders/show.blade.php",
        "line": 24,
    } in artifact["edges"]
    assert {
        "kind": "blade_wire_model_property",
        "from": "livewire_property:status",
        "to": "livewire_property:App\\Livewire\\OrdersStatus.status",
        "livewire_alias": "orders-status",
        "livewire_class": "App\\Livewire\\OrdersStatus",
        "wire_model": "status",
        "livewire_property": "status",
        "livewire_property_type": "string",
        "path": "resources/views/orders/show.blade.php",
        "line": 24,
    } in artifact["edges"]
    assert {
        "kind": "livewire_validation",
        "from": "App\\Livewire\\OrdersStatus",
        "to": "validation:status",
        "livewire_alias": "orders-status",
        "livewire_class": "App\\Livewire\\OrdersStatus",
        "field": "status",
        "validation_rules": ["required", "string"],
        "validation_path": "app/Livewire/OrdersStatus.php",
        "validation_line": 6,
        "path": "app/Livewire/OrdersStatus.php",
        "line": 6,
    } in artifact["edges"]
    assert {
        "kind": "blade_wire_model_validation",
        "from": "livewire_property:status",
        "to": "validation:status",
        "livewire_alias": "orders-status",
        "livewire_class": "App\\Livewire\\OrdersStatus",
        "wire_model": "status",
        "livewire_property": "status",
        "field": "status",
        "validation_rules": ["required", "string"],
        "validation_path": "app/Livewire/OrdersStatus.php",
        "validation_line": 6,
        "path": "resources/views/orders/show.blade.php",
        "line": 24,
    } in artifact["edges"]
    assert {
        "kind": "blade_wire_model",
        "from": "view:orders.show",
        "to": "livewire_property:order.status",
        "wire_model": "order.status",
        "wire_modifiers": ["lazy"],
        "path": "resources/views/orders/show.blade.php",
        "line": 26,
    } in artifact["edges"]
    assert {
        "kind": "blade_wire_model_property",
        "from": "livewire_property:order.status",
        "to": "livewire_property:App\\Livewire\\OrdersStatus.order",
        "livewire_alias": "orders-status",
        "livewire_class": "App\\Livewire\\OrdersStatus",
        "wire_model": "order.status",
        "livewire_property": "order",
        "livewire_property_type": "array",
        "path": "resources/views/orders/show.blade.php",
        "line": 26,
    } in artifact["edges"]
    assert {
        "kind": "livewire_validation",
        "from": "App\\Livewire\\OrdersStatus",
        "to": "validation:order.status",
        "livewire_alias": "orders-status",
        "livewire_class": "App\\Livewire\\OrdersStatus",
        "field": "order.status",
        "validation_rules": ["required", "string"],
        "validation_path": "app/Livewire/OrdersStatus.php",
        "validation_line": 6,
        "path": "app/Livewire/OrdersStatus.php",
        "line": 6,
    } in artifact["edges"]
    assert {
        "kind": "blade_wire_model_validation",
        "from": "livewire_property:order.status",
        "to": "validation:order.status",
        "livewire_alias": "orders-status",
        "livewire_class": "App\\Livewire\\OrdersStatus",
        "wire_model": "order.status",
        "livewire_property": "order",
        "field": "order.status",
        "validation_rules": ["required", "string"],
        "validation_path": "app/Livewire/OrdersStatus.php",
        "validation_line": 6,
        "path": "resources/views/orders/show.blade.php",
        "line": 26,
    } in artifact["edges"]
    assert {
        "kind": "blade_alpine_data",
        "from": "view:orders.show",
        "to": "alpine_state:filters",
        "alpine_data_key": "filters",
        "alpine_data_source": "object",
        "path": "resources/views/orders/show.blade.php",
        "line": 27,
    } in artifact["edges"]
    assert {
        "kind": "blade_alpine_data",
        "from": "view:orders.show",
        "to": "alpine_state:open",
        "alpine_data_key": "open",
        "alpine_data_source": "object",
        "path": "resources/views/orders/show.blade.php",
        "line": 27,
    } in artifact["edges"]
    assert {
        "kind": "blade_alpine_model",
        "from": "view:orders.show",
        "to": "alpine_state:filters.status",
        "alpine_model": "filters.status",
        "alpine_modifiers": ["debounce"],
        "path": "resources/views/orders/show.blade.php",
        "line": 27,
    } in artifact["edges"]
    assert {
        "kind": "blade_alpine_model_data",
        "from": "alpine_state:filters.status",
        "to": "alpine_state:filters",
        "alpine_model": "filters.status",
        "alpine_data_key": "filters",
        "path": "resources/views/orders/show.blade.php",
        "line": 27,
    } in artifact["edges"]
    assert {
        "kind": "blade_alpine_action",
        "from": "view:orders.show",
        "to": "alpine_action:applyFilters",
        "alpine_action": "applyFilters",
        "alpine_event": "click",
        "alpine_modifiers": ["prevent"],
        "path": "resources/views/orders/show.blade.php",
        "line": 28,
    } in artifact["edges"]
    assert {
        "kind": "blade_alpine_data_factory",
        "from": "view:orders.show",
        "to": "alpine_factory:filtersForm",
        "alpine_data_factory": "filtersForm",
        "alpine_data_source": "factory",
        "path": "resources/views/orders/show.blade.php",
        "line": 29,
    } in artifact["edges"]
    assert {
        "kind": "blade_wire_action",
        "from": "view:orders.show",
        "to": "livewire_action:saveOrder",
        "wire_action": "saveOrder",
        "wire_event": "click",
        "wire_modifiers": ["debounce"],
        "path": "resources/views/orders/show.blade.php",
        "line": 25,
    } in artifact["edges"]
    assert {
        "kind": "blade_wire_action_method",
        "from": "livewire_action:saveOrder",
        "to": "OrdersStatus@saveOrder",
        "livewire_alias": "orders-status",
        "livewire_class": "App\\Livewire\\OrdersStatus",
        "wire_action": "saveOrder",
        "path": "resources/views/orders/show.blade.php",
        "line": 25,
    } in artifact["edges"]
    assert {
        "kind": "blade_csrf_token",
        "from": "view:orders.show",
        "to": "csrf:present",
        "csrf": "present",
        "path": "resources/views/orders/show.blade.php",
        "line": 7,
    } in artifact["edges"]
    assert {
        "kind": "blade_form_method",
        "from": "view:orders.show",
        "to": "http_method:PUT",
        "form_method": "PUT",
        "path": "resources/views/orders/show.blade.php",
        "line": 8,
    } in artifact["edges"]
    assert {
        "kind": "blade_form_route_method",
        "from": "view:orders.show",
        "to": "route:invoices.update",
        "route_name": "invoices.update",
        "form_method": "PUT",
        "route_method": "PUT",
        "route_method_match": True,
        "path": "resources/views/orders/show.blade.php",
        "line": 8,
    } in artifact["edges"]
    assert {
        "kind": "blade_form_route_method",
        "from": "view:orders.show",
        "to": "route:invoices.store",
        "route_name": "invoices.store",
        "form_method": "POST",
        "route_method": "POST",
        "route_method_match": True,
        "path": "resources/views/orders/show.blade.php",
        "line": 10,
    } in artifact["edges"]
    assert ("model_table", "App\\Models\\Order", "table:orders") in edges
    assert ("model_fillable", "App\\Models\\Order", "table:orders.customer_id") in edges
    assert ("model_fillable", "App\\Models\\Order", "table:orders.status") in edges
    assert ("model_guarded", "App\\Models\\Order", "table:orders.internal_note") in edges
    assert ("model_hidden", "App\\Models\\Order", "table:orders.internal_note") in edges
    assert ("model_visible", "App\\Models\\Order", "table:orders.status") in edges
    assert ("model_visible", "App\\Models\\Order", "table:orders.display_status") in edges
    assert ("model_appended_attribute", "App\\Models\\Order", "model_attribute:App\\Models\\Order.display_status") in edges
    assert ("model_cast", "App\\Models\\Order", "table:orders.status") in edges
    assert ("model_cast", "App\\Models\\Order", "table:orders.customer_id") in edges
    assert ("model_trait", "App\\Models\\Order", "Illuminate\\Database\\Eloquent\\SoftDeletes") in edges
    assert ("api_resource_model", "App\\Http\\Resources\\OrderResource", "App\\Models\\Order") in edges
    assert ("api_resource_table", "App\\Http\\Resources\\OrderResource", "table:orders") in edges
    assert ("api_resource_field", "App\\Http\\Resources\\OrderResource", "response_field:id") in edges
    assert ("api_resource_field", "App\\Http\\Resources\\OrderResource", "response_field:status") in edges
    assert ("api_resource_ref", "App\\Http\\Controllers\\OrderController", "App\\Http\\Resources\\OrderResource") in edges
    assert ("api_resource_ref", "OrderController@show", "App\\Http\\Resources\\OrderResource") in edges
    assert {
        "kind": "api_resource_field",
        "from": "App\\Http\\Resources\\OrderResource",
        "to": "response_field:status",
        "field": "status",
        "model": "App\\Models\\Order",
        "table": "orders",
        "path": "app/Http/Resources/OrderResource.php",
        "line": 6,
    } in artifact["edges"]
    assert {
        "kind": "api_resource_ref",
        "from": "OrderController@show",
        "to": "App\\Http\\Resources\\OrderResource",
        "class_context": "App\\Http\\Controllers\\OrderController",
        "resource_method": "make",
        "model": "App\\Models\\Order",
        "table": "orders",
        "path": "app/Http/Controllers/OrderController.php",
        "line": 25,
    } in artifact["edges"]
    assert {
        "kind": "model_appended_attribute",
        "from": "App\\Models\\Order",
        "to": "model_attribute:App\\Models\\Order.display_status",
        "field": "display_status",
        "property": "appends",
        "table": "orders",
        "path": "app/Models/Order.php",
        "line": 11,
    } in artifact["edges"]
    assert ("model_accessor", "App\\Models\\Order", "table:orders.display_status") in edges
    assert ("model_accessor", "App\\Models\\Order", "table:orders.normalized_status") in edges
    assert ("model_mutator", "App\\Models\\Order", "table:orders.normalized_status") in edges
    assert ("model_scope", "App\\Models\\Order", "scope:App\\Models\\Order.recent") in edges
    assert ("scope_method", "scope:App\\Models\\Order.recent", "Order@scopeRecent") in edges
    assert ("eloquent_scope_call", "App\\Http\\Controllers\\OrderController", "scope:App\\Models\\Order.recent") in edges
    assert ("eloquent_scope_call", "OrderController@show", "scope:App\\Models\\Order.recent") in edges
    assert {
        "kind": "model_cast",
        "from": "App\\Models\\Order",
        "to": "table:orders.status",
        "field": "status",
        "cast_type": "string",
        "table": "orders",
        "path": "app/Models/Order.php",
        "line": 12,
    } in artifact["edges"]
    assert {
        "kind": "model_cast",
        "from": "App\\Models\\Order",
        "to": "table:orders.customer_id",
        "field": "customer_id",
        "cast_type": "integer",
        "table": "orders",
        "path": "app/Models/Order.php",
        "line": 14,
    } in artifact["edges"]
    assert {
        "kind": "model_trait",
        "from": "App\\Models\\Order",
        "to": "Illuminate\\Database\\Eloquent\\SoftDeletes",
        "trait_class": "Illuminate\\Database\\Eloquent\\SoftDeletes",
        "trait_short_name": "SoftDeletes",
        "table": "orders",
        "path": "app/Models/Order.php",
        "line": 31,
    } in artifact["edges"]
    assert {
        "kind": "model_accessor",
        "from": "App\\Models\\Order",
        "to": "table:orders.display_status",
        "field": "display_status",
        "direction": "get",
        "attribute_style": "classic",
        "attribute_method": "getDisplayStatusAttribute",
        "table": "orders",
        "path": "app/Models/Order.php",
        "line": 16,
    } in artifact["edges"]
    assert {
        "kind": "model_mutator",
        "from": "App\\Models\\Order",
        "to": "table:orders.normalized_status",
        "field": "normalized_status",
        "direction": "set",
        "attribute_style": "attribute_object",
        "attribute_method": "normalizedStatus",
        "table": "orders",
        "path": "app/Models/Order.php",
        "line": 19,
    } in artifact["edges"]
    assert {
        "kind": "eloquent_scope_call",
        "from": "OrderController@show",
        "to": "scope:App\\Models\\Order.recent",
        "class_context": "App\\Http\\Controllers\\OrderController",
        "scope": "recent",
        "model": "App\\Models\\Order",
        "table": "orders",
        "path": "app/Http/Controllers/OrderController.php",
        "line": 24,
    } in artifact["edges"]
    assert ("policy_for", "App\\Models\\Order", "App\\Policies\\OrderPolicy") in edges
    assert {
        "kind": "policy_for",
        "from": "App\\Models\\Order",
        "to": "App\\Policies\\OrderPolicy",
        "source": "policies_property",
        "path": "app/Providers/AuthServiceProvider.php",
        "line": 13,
    } in artifact["edges"]
    assert {
        "kind": "policy_for",
        "from": "App\\Models\\Invoice",
        "to": "App\\Policies\\InvoicePolicy",
        "source": "gate_policy",
        "path": "app/Providers/AuthServiceProvider.php",
        "line": 21,
    } in artifact["edges"]
    assert ("container_binding", "App\\Contracts\\OrderFormatter", "App\\Services\\OrderService") in edges
    assert ("observed_by", "App\\Models\\Order", "App\\Observers\\OrderObserver") in edges
    assert ("observed_by_method", "App\\Models\\Order", "OrderObserver@updated") in edges
    assert {
        "kind": "observed_by_method",
        "from": "App\\Models\\Order",
        "to": "OrderObserver@updated",
        "observer_class": "App\\Observers\\OrderObserver",
        "observer_method": "updated",
        "lifecycle_event": "updated",
        "table": "orders",
        "path": "app/Providers/AuthServiceProvider.php",
        "line": 19,
    } in artifact["edges"]
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
    assert "$formatter->format" not in str(artifact)
    assert "$this->orders->format" not in str(artifact)
    assert "private OrderService" not in str(artifact)
    assert "throw new" not in str(artifact)
    assert "OrderLockedException();" not in str(artifact)
    assert "response()->json" not in str(artifact)
    assert "redirect()->route" not in str(artifact)
    assert "fn ()" not in str(artifact)
    assert "cached" not in str(artifact)
    assert "session()->flash" not in str(artifact)
    assert "Order queued" not in str(artifact)
    assert "token=secret" not in str(artifact)
    assert "https://api.example.test/orders/sync?token=secret" not in str(artifact)
    assert "secret csv payload" not in str(artifact)
    assert "private fallback note" not in str(artifact)
    assert "$request->hasFile" not in str(artifact)
    assert "private cookie value" not in str(artifact)
    assert "locked" not in str(artifact)
    assert "return view('orders.show'" not in str(artifact)
    assert "$this->app->singleton" not in str(artifact)
    assert "Order::observe" not in str(artifact)
    assert "Broadcast::channel" not in str(artifact)
    assert "Route::apiResource" not in str(artifact)
    assert "Schema::create" not in str(artifact)
    assert "config('services.orders.cache')" not in str(artifact)
    assert "order payment gateway degraded" not in str(artifact)
    assert "$this->authorize" not in str(artifact)
    assert "pending" not in str(artifact)
    assert "paid" not in str(artifact)
    assert "rolled_back_secret" not in str(artifact)
    assert "function ()" not in str(artifact)
    assert "$request->validate" not in str(artifact)
    assert "exists:customers,id" not in str(artifact)
    assert "DB::table" not in str(artifact)
    assert "protected $fillable" not in str(artifact)
    assert "protected $guarded" not in str(artifact)
    assert "protected $hidden" not in str(artifact)
    assert "protected $visible" not in str(artifact)
    assert "protected $appends" not in str(artifact)
    assert "protected $casts" not in str(artifact)
    assert "return ['customer_id'" not in str(artifact)
    assert "return ['id' => $this->id" not in str(artifact)
    assert "strtoupper" not in str(artifact)
    assert "Attribute::make" not in str(artifact)
    assert "return $query->latest" not in str(artifact)
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


def test_populate_backend_ast_resolves_inherited_symfony_controller_routes(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    controllers = tmp_path / "src" / "Controller"
    controllers.mkdir(parents=True)
    (controllers / "AdminController.php").write_text(
        "<?php\n"
        "namespace App\\Controller;\n"
        "abstract class AdminController {\n"
        "    /** @Route(\"/\", name=\"\") */\n"
        "    public function index() {}\n"
        "}\n",
        encoding="utf-8",
    )
    (controllers / "RoleController.php").write_text(
        "<?php\n"
        "namespace App\\Controller;\n"
        "abstract class RoleController extends AdminController {}\n",
        encoding="utf-8",
    )
    (controllers / "WorkerController.php").write_text(
        "<?php\n"
        "namespace App\\Controller;\n"
        "/** @Route(\"/generale/soggetti-attivi\", name=\"contact_flock_roles_worker\") */\n"
        "class WorkerController extends RoleController {}\n",
        encoding="utf-8",
    )

    result = execute_job(
        {
            "job_id": "job_symfony_inherited_route",
            "capability": "populate_backend_ast",
            "payload": {"max_files": 20, "max_symbols": 50, "max_edges": 50},
        },
        workspace_root=tmp_path,
    )

    artifact = result["artifact"]
    inherited = next(
        route
        for route in artifact["routes"]
        if route.get("name") == "contact_flock_roles_worker"
    )
    assert inherited["uri"] == "/generale/soggetti-attivi/"
    assert inherited["handler"] == "WorkerController@index"
    assert inherited["defined_handler"] == "AdminController@index"
    assert inherited["inherited"] is True
    assert (
        "route_handler",
        "route:contact_flock_roles_worker",
        "AdminController@index",
    ) in {
        (edge.get("kind"), edge.get("from"), edge.get("to"))
        for edge in artifact["edges"]
    }
    assert "WorkerController@index" not in {
        symbol.get("name") for symbol in artifact["symbols"]
    }


def test_populate_backend_ast_bounds_symfony_inheritance_cycles(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    controllers = tmp_path / "src" / "Controller"
    controllers.mkdir(parents=True)
    (controllers / "AController.php").write_text(
        "<?php\n"
        "namespace App\\Controller;\n"
        "/** @Route(\"/a\", name=\"a_\") */\n"
        "class AController extends BController {}\n",
        encoding="utf-8",
    )
    (controllers / "BController.php").write_text(
        "<?php\n"
        "namespace App\\Controller;\n"
        "class BController extends AController {\n"
        "    /** @Route(\"/index\", name=\"index\") */\n"
        "    public function index() {}\n"
        "}\n",
        encoding="utf-8",
    )

    result = execute_job(
        {
            "job_id": "job_symfony_cycle",
            "capability": "populate_backend_ast",
            "payload": {"max_files": 20, "max_symbols": 50, "max_edges": 50},
        },
        workspace_root=tmp_path,
    )

    report = result["artifact"]["analysis"]["symfony_inheritance"]
    assert result["status"] == "completed"
    assert report["status"] == "partial"
    assert report["cycles"] >= 1
    assert any(route.get("name") == "a_index" for route in result["artifact"]["routes"])


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
