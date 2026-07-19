from __future__ import annotations

import gzip
import json
import re
import subprocess

import pytest


GRAPH_V2_PROJECT_ID = "01KXJD0SV73EBGWKNE2EK3M4KD"
GRAPH_V2_BINDING_ID = "01KXJD1BDMQ2TFABMVJV6EFE8Q"


@pytest.fixture(autouse=True)
def _supply_legacy_graph_jobs_with_backend_identity(
    monkeypatch, request, tmp_path_factory, _hermetic_environment
):
    """Run pre-cutover graph fixtures under a schema-valid backend binding."""

    del _hermetic_environment
    monkeypatch.setenv("HERMES_HOME", str(tmp_path_factory.mktemp("graph-v2-home")))
    monkeypatch.delenv("HADES_HOME", raising=False)
    if request.node.name == "test_graph_v2_rejects_missing_backend_binding_identity":
        return
    import hermes_cli.hades_backend_jobs as jobs
    original = jobs._execute_populate_backend_ast

    def execute_with_binding(job, workspace_root):
        copied = {**job, "payload": dict(job.get("payload") or {})}
        copied["payload"].setdefault("project_id", GRAPH_V2_PROJECT_ID)
        copied["payload"].setdefault("workspace_binding_id", GRAPH_V2_BINDING_ID)
        return original(copied, workspace_root)

    monkeypatch.setattr(jobs, "_execute_populate_backend_ast", execute_with_binding)


def _materialize_graph_v2(result: dict) -> dict:
    """Reconstruct and validate the canonical artifact behind a job descriptor."""

    from hermes_constants import get_hermes_home
    from hermes_cli.hades_graph_v2.bundle import CHUNK_KINDS, GraphBundleWriter
    from hermes_cli.hades_graph_v2.model import artifact_from_payload
    from hermes_cli.hades_graph_v2.validation import validate_artifact

    descriptor = result["artifact"]
    manifest = descriptor["bundle"]
    project = manifest["project"]
    spool = (
        get_hermes_home()
        / "cache"
        / "hades"
        / "graph-imports"
        / project["project_id"]
        / project["workspace_binding_id"]
        / descriptor["artifact_graph_version"]
    )
    state = GraphBundleWriter().resume_state(spool)
    artifact = {
        "schema": "hades.code_graph.v2",
        **{
            key: manifest[key]
            for key in (
                "generated_at",
                "project",
                "source",
                "graph_contract",
                "frameworks",
                "languages",
            )
        },
        **{kind: [] for kind in CHUNK_KINDS},
    }
    for path in state.chunk_paths:
        chunk = json.loads(gzip.decompress(path.read_bytes()))
        artifact[chunk["kind"]].extend(chunk["records"])
    model = artifact_from_payload(artifact)
    validate_artifact(model)
    return artifact


def _extract_wiki_agent_context(content_markdown: str) -> dict:
    match = re.search(
        r"## Agent Context\s*\n+```json\n(?P<context>\{[^\n]+\})\n```",
        content_markdown,
    )
    assert match is not None
    return json.loads(match.group("context"))


def _symlink_or_skip(link, target, *, target_is_directory=False):
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable in test environment: {exc}")


def test_source_node_ir_rejects_noncanonical_key_and_mismatched_evidence():
    from hermes_cli.hades_graph_v2.model import EvidenceOrigin, NodeKind
    from hermes_cli.hades_index.lifecycle.model import (
        AstLocatorIR,
        IREvidence,
        IRValidationError,
        SourceLocationIR,
        SourceNodeIR,
    )

    locator = AstLocatorIR(
        SourceLocationIR("src/app.py", 1, 2, "a" * 64),
        "root/class_definition[0]",
        0,
    )
    other = AstLocatorIR(
        SourceLocationIR("src/app.py", 3, 4, "a" * 64),
        "root/class_definition[1]",
        0,
    )
    evidence = IREvidence(
        EvidenceOrigin.VERIFIED_FROM_CODE,
        "tree-sitter.lifecycle-v2",
        locator,
        None,
    )

    with pytest.raises(IRValidationError, match="source_node.local_key"):
        SourceNodeIR(
            "not-a-digest",
            "python",
            NodeKind.CLASS,
            "Example",
            "Example",
            None,
            locator,
            evidence,
        )
    with pytest.raises(IRValidationError, match="exact declaration locator"):
        SourceNodeIR(
            "b" * 64,
            "python",
            NodeKind.CLASS,
            "Example",
            "Example",
            None,
            other,
            evidence,
        )


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


def test_graph_v2_source_change_preserves_preexisting_resumable_spool(
    tmp_path, monkeypatch
):
    """A failed staging attempt must never delete an already resumable bundle."""
    from hermes_cli.hades_graph_config import SourceIdentityError
    import hermes_cli.hades_graph_config as graph_config
    from hermes_cli.hades_backend_jobs import execute_job
    from hermes_cli.hades_graph_v2.bundle import GraphBundleWriter

    workspace = tmp_path / "workspace"
    source = workspace / "src" / "app.py"
    source.parent.mkdir(parents=True)
    source.write_text("def before():\n    return 1\n", encoding="utf-8")
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    payload = {
        "project_id": "01KXJD0SV73EBGWKNE2EK3M4KD",
        "workspace_binding_id": "01KXJD1BDMQ2TFABMVJV6EFE8Q",
    }
    first = execute_job(
        {"job_id": "job_first", "capability": "populate_backend_ast", "payload": payload},
        workspace_root=workspace,
    )
    version = first["artifact"]["artifact_graph_version"]
    spool = (
        home
        / "cache"
        / "hades"
        / "graph-imports"
        / payload["project_id"]
        / payload["workspace_binding_id"]
        / version
    )
    before = {
        path.name: path.read_bytes()
        for path in spool.iterdir()
        if path.name != ".lock"
    }
    GraphBundleWriter().resume_state(spool)

    original_verify = graph_config.verify_source_unchanged

    def mutate_then_verify(root, config, source_before):
        source.write_text("def after():\n    return 2\n", encoding="utf-8")
        return original_verify(root, config, source_before)

    monkeypatch.setattr(graph_config, "verify_source_unchanged", mutate_then_verify)

    with pytest.raises(SourceIdentityError, match="source_changed_during_index"):
        execute_job(
            {"job_id": "job_changed", "capability": "populate_backend_ast", "payload": payload},
            workspace_root=workspace,
        )

    assert {
        path.name: path.read_bytes()
        for path in spool.iterdir()
        if path.name != ".lock"
    } == before
    GraphBundleWriter().resume_state(spool)
    assert not any(".staging-" in path.name for path in spool.parent.iterdir())


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

    descriptor = result["artifact"]
    artifact = _materialize_graph_v2(result)
    assert result["status"] == "completed"
    assert descriptor["schema"] == "hades.code_graph.v2"
    assert artifact["schema"] == "hades.code_graph.v2"
    assert artifact["graph_contract"]["version"] == "hades.graph_artifact.v2"
    assert artifact["source"] == descriptor["source_identity"]
    assert result["source_slice_candidates"]
    assert {item["reason"] for item in result["source_slice_candidates"]} >= {"domain_data_integration"}
    assert "Built graph lifecycle v2 bundle" in result["summary"]
    assert "return Booking::create" not in str(result["source_slice_candidates"])


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

    descriptor = result["artifact"]
    artifact = _materialize_graph_v2(result)
    assert result["status"] == "completed"
    assert descriptor["schema"] == "hades.code_graph.v2"
    assert {item["name"] for item in artifact["languages"]} == {"php", "typescript"}
    assert {item.get("name") for item in artifact["nodes"]} >= {
        "PhpController",
        "healthHandler",
    }
    # Route-looking syntax without package metadata must not manufacture a
    # framework record or HTTP entrypoint.
    assert not any(
        entrypoint.get("public_path") == "/health"
        for entrypoint in artifact["entrypoints"]
    )


@pytest.mark.parametrize(
    ("filename", "language"),
    (("api.js", "javascript"), ("api.ts", "typescript")),
)
def test_populate_backend_ast_materializes_package_proven_express_route(
    tmp_path, filename, language
):
    from hermes_cli.hades_backend_jobs import execute_job

    (tmp_path / "package.json").write_text(
        '{"dependencies":{"express":"latest"}}', encoding="utf-8"
    )
    route = tmp_path / "server" / filename
    route.parent.mkdir(parents=True)
    route.write_text(
        "import express from 'express';\n"
        "const app = express();\n"
        "const router = express.Router();\n"
        "app.use('/api', router);\n"
        "router.get('/health', healthHandler);\n"
        "export function healthHandler() { return { ok: true }; }\n",
        encoding="utf-8",
    )

    result = execute_job(
        {
            "job_id": "job_express_route",
            "capability": "populate_backend_ast",
            "payload": {"max_files": 20, "max_symbols": 50, "max_edges": 50},
        },
        workspace_root=tmp_path,
    )

    artifact = _materialize_graph_v2(result)
    assert any(
        framework["name"] == "express" and framework["language"] == language
        for framework in artifact["frameworks"]
    )
    assert any(
        entrypoint.get("framework") == "express"
        and entrypoint.get("methods") == ["GET"]
        and entrypoint.get("public_path") == "/api/health"
        for entrypoint in artifact["entrypoints"]
    )


def test_populate_backend_ast_does_not_infer_express_from_route_syntax(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    route = tmp_path / "server" / "api.js"
    route.parent.mkdir(parents=True)
    route.write_text(
        "const app = express();\n"
        "app.get('/health', healthHandler);\n"
        "function healthHandler(req, res) { res.json({ok: true}); }\n",
        encoding="utf-8",
    )

    result = execute_job(
        {
            "job_id": "job_express_syntax_only",
            "capability": "populate_backend_ast",
            "payload": {"max_files": 20, "max_symbols": 50, "max_edges": 50},
        },
        workspace_root=tmp_path,
    )

    artifact = _materialize_graph_v2(result)
    assert not any(item["name"] == "express" for item in artifact["frameworks"])
    assert not any(
        item.get("framework") == "express" for item in artifact["entrypoints"]
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

    artifact = _materialize_graph_v2(result)
    coverage = artifact["graph_contract"]["coverage"]["files"]
    assert coverage["discovered"] == 2
    assert coverage["analyzed"] == 1
    assert coverage["too_large"] == 0
    assert coverage["failed"] == 1
    assert any(
        node.get("kind") == "file"
        and node.get("qualified_name") == "src/oversized.ts"
        and node["properties"]["analysis_status"] == "failed"
        and node["properties"]["omission_reason"] == "parser_failed"
        for node in artifact["nodes"]
    )


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

    artifact = _materialize_graph_v2(result)
    coverage = artifact["graph_contract"]["coverage"]["files"]
    assert "Class1000" in {item.get("name") for item in artifact["nodes"]}
    assert coverage["analyzed"] == 1_001
    assert coverage["budget_omitted"] == 0


def test_populate_backend_ast_honors_payload_file_budget(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    workspace = tmp_path / "workspace"
    source = workspace / "src"
    source.mkdir(parents=True)
    for index in range(3):
        (source / f"module_{index:05d}.py").write_text(
            f"class Module{index}:\n    pass\n", encoding="utf-8"
        )

    result = execute_job(
        {
            "job_id": "job_hard_file_cap",
            "capability": "populate_backend_ast",
            "payload": {"max_files": 2},
        },
        workspace_root=workspace,
    )

    artifact = _materialize_graph_v2(result)
    contract = artifact["graph_contract"]
    coverage = contract["coverage"]["files"]
    reasons = contract["completeness"]["capabilities"]["inventory"]["reasons"]
    assert coverage["analyzed"] == 2
    assert coverage["budget_omitted"] == 1
    assert reasons == [
        {
            "code": "resource_budget_reached",
            "count": 1,
            "language": None,
            "paths_sample": ["src/module_00002.py"],
        }
    ]


def test_populate_backend_ast_hard_clamps_file_count_at_ten_thousand(
    monkeypatch, tmp_path
):
    from dataclasses import replace

    import hermes_cli.hades_index as hades_index
    from hermes_cli.hades_backend_jobs import execute_job
    from hermes_cli.hades_index.tree_sitter_adapter import (
        ParsedFile,
        ParseResult,
        SyntaxIR,
        TreeSitterAdapter,
    )

    source = tmp_path / "src"
    source.mkdir()
    for index in range(10_001):
        source.joinpath(f"module_{index:05d}.py").touch()

    parser_paths: list[str] = []

    def parsed_empty_file(self, path, *, relative_path, language, max_bytes):
        del self, path, max_bytes
        parser_paths.append(relative_path)
        return ParseResult.parsed(
            SyntaxIR(ParsedFile(relative_path, language, (), (), ()), ())
        )

    monkeypatch.setattr(TreeSitterAdapter, "parse_file", parsed_empty_file)
    real_build = hades_index.build_canonical_graph
    captured: dict[str, int] = {}

    def bounded_build(context, results):
        captured["parser_candidates"] = sum(
            item.parser_candidate for item in context.inventory_files
        )
        captured["budget_omitted"] = sum(
            event.omitted_count
            for result in results
            for event in result.coverage_events
            if event.capability.value == "inventory"
            and event.reason_code == "resource_budget_reached"
        )
        retained_paths = {"src/module_00000.py", "src/module_10000.py"}
        bounded_context = replace(
            context,
            inventory_files=tuple(
                item for item in context.inventory_files if item.path in retained_paths
            ),
        )
        bounded_results = tuple(
            replace(
                result,
                coverage_events=tuple(
                    event
                    for event in result.coverage_events
                    if event.path is None or event.path in retained_paths
                ),
            )
            for result in results
        )
        return real_build(bounded_context, bounded_results)

    monkeypatch.setattr(hades_index, "build_canonical_graph", bounded_build)
    result = execute_job(
        {
            "job_id": "job_hard_file_ceiling",
            "capability": "populate_backend_ast",
            "payload": {"max_files": 10_001},
        },
        workspace_root=tmp_path,
    )

    assert result["status"] == "completed"
    assert captured == {"parser_candidates": 10_001, "budget_omitted": 1}
    assert len(parser_paths) == 10_000


def test_populate_backend_ast_honors_payload_aggregate_byte_budget(
    tmp_path,
):
    from hermes_cli.hades_backend_jobs import execute_job

    workspace = tmp_path / "workspace"
    source = workspace / "src"
    source.mkdir(parents=True)
    source.joinpath("a.py").write_text("#" * 700, encoding="utf-8")
    source.joinpath("b.py").write_text("#" * 700, encoding="utf-8")

    result = execute_job(
        {
            "job_id": "job_hard_byte_cap",
            "capability": "populate_backend_ast",
            "payload": {
                "max_file_bytes": 1_024,
                "max_total_bytes": 900,
            },
        },
        workspace_root=workspace,
    )

    artifact = _materialize_graph_v2(result)
    contract = artifact["graph_contract"]
    coverage = contract["coverage"]["files"]
    reasons = contract["completeness"]["capabilities"]["inventory"]["reasons"]
    assert coverage["analyzed"] == 1
    assert coverage["budget_omitted"] == 1
    assert reasons == [
        {
            "code": "resource_budget_reached",
            "count": 1,
            "language": None,
            "paths_sample": ["src/b.py"],
        }
    ]


def test_populate_backend_ast_uses_configured_aggregate_byte_ceiling(
    monkeypatch, tmp_path
):
    from hermes_cli.hades_backend_jobs import execute_job

    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    (home / "config.yaml").write_text(
        "hades:\n  graph_index:\n    max_total_source_bytes: 1048576\n",
        encoding="utf-8",
    )
    workspace.joinpath("a.py").write_text("#" * 600_000, encoding="utf-8")
    workspace.joinpath("b.py").write_text("#" * 600_000, encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))

    artifact = _materialize_graph_v2(
        execute_job(
            {
                "job_id": "job_configured_aggregate_ceiling",
                "capability": "populate_backend_ast",
                "payload": {"max_total_bytes": 2_000_000},
            },
            workspace_root=workspace,
        )
    )
    coverage = artifact["graph_contract"]["coverage"]["files"]
    assert coverage["analyzed"] == 1
    assert coverage["budget_omitted"] == 1


def test_populate_backend_ast_honors_payload_per_file_budget(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    workspace = tmp_path / "workspace"
    source = workspace / "src"
    source.mkdir(parents=True)
    oversized = source / "oversized.py"
    oversized.write_text("#" * 1_025, encoding="utf-8")

    result = execute_job(
        {
            "job_id": "job_hard_per_file_cap",
            "capability": "populate_backend_ast",
            "payload": {"max_file_bytes": 1_024},
        },
        workspace_root=workspace,
    )

    artifact = _materialize_graph_v2(result)
    assert any(
        node["kind"] == "file"
        and node["qualified_name"] == "src/oversized.py"
        and node["properties"]["omission_reason"] == "file_too_large"
        for node in artifact["nodes"]
    )
    reasons = artifact["graph_contract"]["completeness"]["capabilities"][
        "inventory"
    ]["reasons"]
    assert reasons == [
        {
            "code": "file_too_large",
            "count": 1,
            "language": None,
            "paths_sample": ["src/oversized.py"],
        }
    ]


def test_populate_backend_ast_uses_configured_per_file_byte_ceiling(
    monkeypatch, tmp_path
):
    from hermes_cli.hades_backend_jobs import execute_job

    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    (home / "config.yaml").write_text(
        "hades:\n  graph_index:\n    max_file_bytes: 1024\n",
        encoding="utf-8",
    )
    workspace.joinpath("oversized.py").write_text("#" * 1_025, encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))

    artifact = _materialize_graph_v2(
        execute_job(
            {
                "job_id": "job_configured_per_file_ceiling",
                "capability": "populate_backend_ast",
                "payload": {"max_file_bytes": 2_048},
            },
            workspace_root=workspace,
        )
    )
    file_node = next(node for node in artifact["nodes"] if node["kind"] == "file")
    assert file_node["properties"]["analysis_status"] == "too_large"
    assert file_node["properties"]["omission_reason"] == "file_too_large"


def test_populate_backend_ast_closes_invalid_symlink_inventory_reason(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("class Secret:\n    pass\n", encoding="utf-8")
    _symlink_or_skip(workspace / "escape.py", outside)

    artifact = _materialize_graph_v2(
        execute_job(
            {
                "job_id": "job_graph_symlink_ledger",
                "capability": "populate_backend_ast",
                "payload": {},
            },
            workspace_root=workspace,
        )
    )

    symlink = next(
        node for node in artifact["nodes"] if node["qualified_name"] == "escape.py"
    )
    assert symlink["properties"]["analysis_status"] == "failed"
    assert symlink["properties"]["omission_reason"] == "symlink_unavailable"
    inventory = artifact["graph_contract"]["completeness"]["capabilities"][
        "inventory"
    ]
    assert inventory["status"] == "partial"
    assert inventory["reasons"] == [
        {
            "code": "symlink_unavailable",
            "count": 1,
            "language": None,
            "paths_sample": ["escape.py"],
        }
    ]


def test_populate_backend_ast_closes_unavailable_submodule_inventory_reason(
    tmp_path,
):
    from hermes_cli.hades_backend_jobs import execute_job

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    commit = "a" * 40
    try:
        subprocess.run(
            ["git", "init"],
            cwd=workspace,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            [
                "git",
                "update-index",
                "--add",
                "--cacheinfo",
                f"160000,{commit},lib",
            ],
            cwd=workspace,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        pytest.skip(f"git index fixture unavailable: {exc}")

    artifact = _materialize_graph_v2(
        execute_job(
            {
                "job_id": "job_graph_submodule_ledger",
                "capability": "populate_backend_ast",
                "payload": {},
            },
            workspace_root=workspace,
        )
    )

    submodule = next(
        node for node in artifact["nodes"] if node["qualified_name"] == "lib"
    )
    assert submodule["properties"]["analysis_status"] == "failed"
    assert submodule["properties"]["omission_reason"] == "submodule_unavailable"
    assert artifact["graph_contract"]["coverage"]["files"]["failed"] == 1
    inventory = artifact["graph_contract"]["completeness"]["capabilities"][
        "inventory"
    ]
    assert inventory["status"] == "partial"
    assert inventory["reasons"] == [
        {
            "code": "submodule_unavailable",
            "count": 1,
            "language": None,
            "paths_sample": ["lib"],
        }
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

    result = execute_job(
        {
            "job_id": "job_single_language_test_inventory_cap",
            "capability": "populate_backend_ast",
            "payload": {},
        },
        workspace_root=workspace,
    )
    artifact = _materialize_graph_v2(result)
    coverage = artifact["graph_contract"]["coverage"]["files"]
    assert coverage["analyzed"] == 501
    assert sum(node["kind"] == "file" for node in artifact["nodes"]) == 501


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

    result = execute_job(
        {
            "job_id": "job_single_language_route_inventory_cap",
            "capability": "populate_backend_ast",
            "payload": {},
        },
        workspace_root=workspace,
    )
    artifact = _materialize_graph_v2(result)
    assert {item["name"] for item in artifact["languages"]} == {"typescript"}
    assert not artifact["entrypoints"]
    assert any(
        node["kind"] == "file" and node["qualified_name"] == "src/routes.ts"
        for node in artifact["nodes"]
    )


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

    result = execute_job(
        {
            "job_id": "job_polyglot_inventory_cap",
            "capability": "populate_backend_ast",
            "payload": {},
        },
        workspace_root=workspace,
    )
    artifact = _materialize_graph_v2(result)
    assert {item["name"] for item in artifact["languages"]} == {"python", "typescript"}
    assert not artifact["entrypoints"]
    assert sum(node["kind"] == "file" for node in artifact["nodes"]) == 503


def test_populate_backend_ast_closes_exact_symbol_cap_ledger(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    workspace = tmp_path / "workspace"
    source = workspace / "src"
    source.mkdir(parents=True)
    source.joinpath("graph.py").write_text(
        "class Service:\n"
        "    def first(self):\n        pass\n"
        "    def second(self):\n        pass\n"
        "    def third(self):\n        pass\n",
        encoding="utf-8",
    )

    result = execute_job(
        {
            "job_id": "job_hard_graph_caps",
            "capability": "populate_backend_ast",
            "payload": {"max_symbols": 2, "max_edges": 10},
        },
        workspace_root=workspace,
    )

    artifact = _materialize_graph_v2(result)
    symbol_resolution = artifact["graph_contract"]["completeness"]["capabilities"][
        "symbol_resolution"
    ]
    assert symbol_resolution["status"] == "partial"
    assert symbol_resolution["reasons"] == [
        {
            "code": "resource_budget_reached",
            "count": 2,
            "language": None,
            "paths_sample": ["src/graph.py"],
        }
    ]
    file_node = next(
        node for node in artifact["nodes"] if node["qualified_name"] == "src/graph.py"
    )
    assert file_node["properties"]["analysis_status"] == "analyzed"


def test_populate_backend_ast_hard_clamps_symbols_at_five_thousand(
    monkeypatch, tmp_path
):
    from dataclasses import replace

    import hermes_cli.hades_index.lifecycle.assembler as assembler
    from hermes_cli.hades_backend_jobs import execute_job
    from hermes_cli.hades_index.tree_sitter_adapter import (
        ParsedFile,
        ParseResult,
        StructuralSymbol,
        SyntaxIR,
        TreeSitterAdapter,
    )

    source = tmp_path / "graph.py"
    source.write_text("pass\n", encoding="utf-8")
    parser_symbols = tuple(
        StructuralSymbol(
            f"Symbol{index}",
            "class",
            1,
            1,
            structural_path=f"root/class_definition/{index}",
        )
        for index in range(5_001)
    )

    def parsed_symbol_ceiling(self, path, *, relative_path, language, max_bytes):
        del self, path, max_bytes
        return ParseResult.parsed(
            SyntaxIR(
                ParsedFile(relative_path, language, parser_symbols, (), ()),
                (),
            )
        )

    monkeypatch.setattr(TreeSitterAdapter, "parse_file", parsed_symbol_ceiling)
    real_assemble = assembler.assemble_graph_v2_adapter_result
    captured: dict[str, int] = {}

    def bounded_assemble(context, syntax, *, parse_coverage=(), registry=None):
        captured["retained_symbols"] = sum(len(item.symbols) for item in syntax)
        bounded_syntax = tuple(
            replace(
                item,
                parsed_file=replace(item.parsed_file, symbols=()),
            )
            for item in syntax
        )
        return real_assemble(
            context,
            bounded_syntax,
            parse_coverage=parse_coverage,
            registry=registry,
        )

    monkeypatch.setattr(
        assembler, "assemble_graph_v2_adapter_result", bounded_assemble
    )
    artifact = _materialize_graph_v2(
        execute_job(
            {
                "job_id": "job_hard_symbol_ceiling",
                "capability": "populate_backend_ast",
                "payload": {"max_symbols": 5_001},
            },
            workspace_root=tmp_path,
        )
    )
    assert captured == {"retained_symbols": 5_000}
    reason = next(
        item
        for item in artifact["graph_contract"]["completeness"]["capabilities"][
            "symbol_resolution"
        ]["reasons"]
        if item["code"] == "resource_budget_reached"
    )
    assert reason["count"] == 1


def test_populate_backend_ast_closes_exact_edge_cap_ledger(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    source = tmp_path / "src"
    source.mkdir()
    source.joinpath("graph.py").write_text(
        "class Service:\n"
        "    def first(self):\n        pass\n"
        "    def second(self):\n        pass\n"
        "    def third(self):\n        pass\n",
        encoding="utf-8",
    )
    artifact = _materialize_graph_v2(
        execute_job(
            {
                "job_id": "job_edge_cap_ledger",
                "capability": "populate_backend_ast",
                "payload": {"max_symbols": 10, "max_edges": 1},
            },
            workspace_root=tmp_path,
        )
    )

    call_graph = artifact["graph_contract"]["completeness"]["capabilities"][
        "call_graph"
    ]
    assert len(artifact["edges"]) == 1
    assert call_graph["status"] == "partial"
    budget_reason = next(
        reason
        for reason in call_graph["reasons"]
        if reason["code"] == "resource_budget_reached"
    )
    assert budget_reason["count"] == 8
    assert budget_reason["paths_sample"] == ["src/graph.py"]
    assert artifact["graph_contract"]["coverage"]["records"][
        "omitted_by_bundle_budget"
    ] == 6


def test_populate_backend_ast_hard_clamps_edges_at_ten_thousand(
    monkeypatch, tmp_path
):
    import hermes_cli.hades_index as hades_index
    import hermes_cli.hades_graph_v2.pruning as graph_pruning
    from hermes_cli.hades_backend_jobs import execute_job
    from hermes_cli.hades_graph_v2 import artifact_from_payload
    from hermes_cli.hades_graph_v2.validation import validate_artifact
    from tests.hermes_cli.test_hades_graph_budget_pruner import _many_edge_artifact

    source = tmp_path / "graph.py"
    source.write_text("pass\n", encoding="utf-8")
    canonical = _many_edge_artifact(10_001)
    monkeypatch.setattr(
        hades_index,
        "build_canonical_graph",
        lambda _context, _results: artifact_from_payload(canonical),
    )
    real_finalize = graph_pruning._finalize_candidate
    finalize_calls = 0

    def tracked_finalize(*args, **kwargs):
        nonlocal finalize_calls
        finalize_calls += 1
        return real_finalize(*args, **kwargs)

    monkeypatch.setattr(graph_pruning, "_finalize_candidate", tracked_finalize)
    artifact = _materialize_graph_v2(
        execute_job(
            {
                "job_id": "job_hard_edge_ceiling",
                "capability": "populate_backend_ast",
                "payload": {"max_edges": 10_001},
            },
            workspace_root=tmp_path,
        )
    )

    validate_artifact(artifact)
    assert finalize_calls == 1
    assert len(artifact["edges"]) == 10_000
    assert artifact["graph_contract"]["coverage"]["records"][
        "omitted_by_bundle_budget"
    ] == 1
    reason = next(
        item
        for item in artifact["graph_contract"]["completeness"]["capabilities"][
            "call_graph"
        ]["reasons"]
        if item["code"] == "resource_budget_reached"
    )
    assert reason["count"] == 1


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
    test_file.write_text(
        "<?php\nclass BookingTest { public function test_stores_bookings() {} }\n",
        encoding="utf-8",
    )

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
    assert {page["source_status"] for page in pages} == {"needs_verification"}
    assert "Project Overview" in overview["title"]
    assert "Raw source is not embedded" in overview["content_markdown"]
    assert any(ref["kind"] == "artifact_ref" for ref in overview["evidence_refs"])
    assert any(ref.get("path") == "composer.json" for ref in overview["evidence_refs"])
    assert any(slug.endswith("-entrypoints") for slug in slugs)
    assert any(slug.endswith("-data-model") for slug in slugs)
    assert any(slug.endswith("-symbol-map") for slug in slugs)
    assert any(slug.endswith("-tests-quality") for slug in slugs)
    assert "return Booking::create" not in str(result)
    for page in pages:
        content = page["content_markdown"]
        assert "## Human Summary" in content
        assert "## Agent Context" in content
        assert "```json" in content
        assert len(content) <= 24_000
        context = _extract_wiki_agent_context(content)
        assert context["schema"] == "hades.wiki.agent_context.v1"
        assert context["page_kind"]
        assert context["evidence_kinds"]
        assert context["raw_source_included"] is False

    repeated = execute_job(
        {
            "job_id": "job_wiki_repeated",
            "capability": "populate_project_wiki",
            "payload": {"max_files": 80, "max_symbols": 200},
        },
        workspace_root=tmp_path,
    )
    assert [page["content_markdown"] for page in repeated["pages"]] == [
        page["content_markdown"] for page in pages
    ]


def test_wiki_artifact_evidence_uses_authoritative_canonical_payload_hash():
    from hermes_cli.hades_backend_jobs import _artifact_evidence
    from hermes_cli.hades_backend_sync import _artifact_payload_hash

    payload = {
        "schema": "hades.git_tree.v1",
        "metadata": {
            "ratio": 1.0,
            "labels": ["β", "a/b"],
            "nested": {"z": "last", "a": "first"},
        },
        "head_commit": "a" * 40,
        "files": [{"sha256": "1" * 64, "path": "src/Foo.php"}],
    }

    assert _artifact_evidence(payload)["sha256"] == _artifact_payload_hash(payload)


@pytest.mark.parametrize(
    ("filename", "source"),
    [
        ("service.py", "class Service:\n    marker = 'RAW_SOURCE_SENTINEL'\n"),
        ("service.ts", "export class Service { marker = 'RAW_SOURCE_SENTINEL'; }\n"),
        ("service.go", 'package service\nconst marker = "RAW_SOURCE_SENTINEL"\n'),
        ("service.rs", 'const MARKER: &str = "RAW_SOURCE_SENTINEL";\n'),
        ("Service.java", 'class Service { String marker = "RAW_SOURCE_SENTINEL"; }\n'),
        ("Service.php", "<?php\nclass Service { private string $marker = 'RAW_SOURCE_SENTINEL'; }\n"),
    ],
    ids=["python", "typescript", "go", "rust", "java", "php"],
)
def test_populate_project_wiki_dual_audience_contract_is_language_agnostic(
    tmp_path,
    filename,
    source,
):
    from hermes_cli.hades_backend_jobs import execute_job

    (tmp_path / filename).write_text(source, encoding="utf-8")

    result = execute_job(
        {
            "job_id": f"job_wiki_{filename}",
            "capability": "populate_project_wiki",
            "payload": {"max_files": 20, "max_symbols": 20},
        },
        workspace_root=tmp_path,
    )

    assert result["pages"]
    assert "RAW_SOURCE_SENTINEL" not in str(result)
    for page in result["pages"]:
        content = page["content_markdown"]
        context = _extract_wiki_agent_context(content)
        assert context == {
            "evidence_kinds": sorted(
                {ref["kind"] for ref in page["evidence_refs"]}
            ),
            "page_kind": context["page_kind"],
            "raw_source_included": False,
            "schema": "hades.wiki.agent_context.v1",
        }
        assert len(content) <= 24_000


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

    descriptor = result["artifact"]
    artifact = _materialize_graph_v2(result)
    nodes = {(item["kind"], item["name"]) for item in artifact["nodes"]}

    assert result["status"] == "completed"
    assert descriptor["schema"] == "hades.code_graph.v2"
    assert {item["name"] for item in artifact["languages"]} == {"python"}
    assert artifact["entrypoints"] == []
    assert ("class", "Service") in nodes
    assert ("function", "helper") in nodes
    assert "return 1" not in str(result)


def test_populate_backend_ast_extracts_python_web_graph_without_source(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    (tmp_path / "app").mkdir()
    (tmp_path / "project").mkdir()
    (tmp_path / "requirements.txt").write_text("Django==5.2\nfastapi==0.116\n", encoding="utf-8")
    (tmp_path / "project" / "settings.py").write_text(
        "ROOT_URLCONF = 'project.urls'\nMIDDLEWARE = []\n",
        encoding="utf-8",
    )
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

    descriptor = result["artifact"]
    artifact = _materialize_graph_v2(result)
    node_names = {item["name"] for item in artifact["nodes"]}
    entrypoints = {
        (item["framework"], item["public_path"])
        for item in artifact["entrypoints"]
    }

    assert result["status"] == "completed"
    assert descriptor["schema"] == "hades.code_graph.v2"
    assert {item["name"] for item in artifact["languages"]} == {"python"}
    assert {"show_order", "order_detail", "OrderCreateView", "OrderService", "Customer", "Order"} <= node_names
    assert ("django", "/orders/{pk}/") in entrypoints
    assert {"customers", "orders"} <= {
        item["name"] for item in artifact["nodes"] if item["kind"] == "table"
    }
    assert any(edge["relation"] == "references" for edge in artifact["edges"])
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

    descriptor = result["artifact"]
    artifact = _materialize_graph_v2(result)
    assert result["status"] == "completed"
    assert descriptor["schema"] == "hades.code_graph.v2"
    assert {item["name"] for item in artifact["languages"]} == {"python"}
    assert "Invoice" in {item["name"] for item in artifact["nodes"]}
    assert "billing_invoice" in {
        item["name"] for item in artifact["nodes"] if item["kind"] == "table"
    }
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

    descriptor = result["artifact"]
    artifact = _materialize_graph_v2(result)
    assert result["status"] == "completed"
    assert descriptor["schema"] == "hades.code_graph.v2"
    assert {item["name"] for item in artifact["languages"]} == {"python"}
    assert {"Customer", "Order"} <= {item["name"] for item in artifact["nodes"]}
    assert {"customers", "orders"} <= {
        item["name"] for item in artifact["nodes"] if item["kind"] == "table"
    }
    assert any(edge["relation"] == "references" for edge in artifact["edges"])
    assert "Column(" not in str(artifact)
    assert "ForeignKey(" not in str(artifact)


def test_populate_backend_ast_extracts_laravel_php_graph_without_source(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "composer.json").write_text(
        '{"require":{"laravel/framework":"^11.0"}}', encoding="utf-8"
    )
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

    descriptor = result["artifact"]
    artifact = _materialize_graph_v2(result)
    node_names = {item["name"] for item in artifact["nodes"]}

    assert result["status"] == "completed"
    assert descriptor["schema"] == "hades.code_graph.v2"
    assert {item["name"] for item in artifact["languages"]} == {"php"}
    assert {
        "OrderController",
        "InvoiceController",
        "Order",
        "OrderPolicy",
        "OrderService",
        "StoreOrderRequest",
        "OrderResource",
    } <= node_names
    assert any(
        item["framework"] == "laravel" and item["public_path"] == "/orders/{order}"
        for item in artifact["entrypoints"]
    )
    assert "orders" in {
        item["name"] for item in artifact["nodes"] if item["kind"] == "table"
    }
    assert any(
        item["kind"] in {"middleware", "authorization", "validator"}
        for item in artifact["nodes"]
    )
    assert "return view('orders.show'" not in str(artifact)
    assert "Schema::create" not in str(artifact)
    assert "order payment gateway degraded" not in str(artifact)


def test_populate_backend_ast_extracts_symfony_php_graph_without_source(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    workspace = tmp_path / "symfony"
    workspace.mkdir()
    (workspace / "composer.json").write_text(
        '{"require":{"symfony/framework-bundle":"^6.4"}}', encoding="utf-8"
    )
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

    descriptor = result["artifact"]
    artifact = _materialize_graph_v2(result)
    assert result["status"] == "completed"
    assert descriptor["schema"] == "hades.code_graph.v2"
    assert {item["name"] for item in artifact["languages"]} == {"php"}
    assert {"OrderController", "HealthController", "OrderService"} <= {
        item["name"] for item in artifact["nodes"]
    }
    assert {
        (item["framework"], item["public_path"])
        for item in artifact["entrypoints"]
    } >= {("symfony", "/admin/orders/{id}"), ("symfony", "/admin/legacy")}
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

    artifact = _materialize_graph_v2(result)
    assert "WorkerController" in {item["name"] for item in artifact["nodes"]}


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

    artifact = _materialize_graph_v2(result)
    assert result["status"] == "completed"
    assert {"AController", "BController"} <= {
        item["name"] for item in artifact["nodes"]
    }


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

    descriptor = result["artifact"]
    artifact = _materialize_graph_v2(result)
    assert result["status"] == "completed"
    assert descriptor["schema"] == "hades.code_graph.v2"
    assert {"Customer", "Order"} <= {item["name"] for item in artifact["nodes"]}
    assert {"customers", "orders"} <= {
        item["name"] for item in artifact["nodes"] if item["kind"] == "table"
    }
    assert any(edge["relation"] == "references" for edge in artifact["edges"])
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

    descriptor = result["artifact"]
    artifact = _materialize_graph_v2(result)
    entrypoints = {
        (item["framework"], tuple(item["methods"]), item["public_path"])
        for item in artifact["entrypoints"]
    }
    assert result["status"] == "completed"
    assert descriptor["schema"] == "hades.code_graph.v2"
    assert {item["name"] for item in artifact["languages"]} == {"typescript"}
    assert ("nextjs", ("GET",), "/api/orders") in entrypoints
    assert {"OrdersPage", "OrderTable", "healthHandler"} <= {
        item["name"] for item in artifact["nodes"]
    }
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

    descriptor = result["artifact"]
    artifact = _materialize_graph_v2(result)
    assert result["status"] == "completed"
    assert descriptor["schema"] == "hades.code_graph.v2"
    assert {item["name"] for item in artifact["languages"]} == {"prisma"}
    assert {"customers", "orders"} <= {
        item["name"] for item in artifact["nodes"] if item["kind"] == "table"
    }
    assert any(edge["relation"] == "references" for edge in artifact["edges"])
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

    descriptor = result["artifact"]
    artifact = _materialize_graph_v2(result)
    assert result["status"] == "completed"
    assert descriptor["schema"] == "hades.code_graph.v2"
    assert {item["name"] for item in artifact["languages"]} == {"typescript"}
    assert {"customers", "orders"} <= {
        item["name"] for item in artifact["nodes"] if item["kind"] == "table"
    }
    assert any(edge["relation"] == "references" for edge in artifact["edges"])
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

    descriptor = result["artifact"]
    artifact = _materialize_graph_v2(result)
    tables = {
        item["name"]: item
        for item in artifact["nodes"]
        if item.get("kind") == "table"
    }
    assert result["status"] == "completed"
    assert descriptor["schema"] == "hades.code_graph.v2"
    assert {item["name"] for item in artifact["languages"]} == {"sql"}
    assert set(tables) == {"customers", "orders"}
    assert artifact["entrypoints"] == []
    assert tables["orders"]["location"]["path"] == "schema.sql"
    assert any(
        edge["relation"] == "references"
        and edge.get("source_id") == tables["orders"]["id"]
        and edge.get("target_id") == tables["customers"]["id"]
        and edge["flow"] is None
        for edge in artifact["edges"]
    )
    assert "CREATE TABLE" not in str(artifact)
    assert "CONSTRAINT orders_customer_fk" not in str(artifact)


def test_populate_backend_ast_resolves_unique_cross_file_sql_foreign_key(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    (tmp_path / "customers.sql").write_text(
        "CREATE TABLE customers (id INTEGER PRIMARY KEY);\n", encoding="utf-8"
    )
    (tmp_path / "orders.sql").write_text(
        "CREATE TABLE orders (customer_id INTEGER REFERENCES customers(id));\n",
        encoding="utf-8",
    )

    artifact = _materialize_graph_v2(
        execute_job(
            {
                "job_id": "job_sql_cross_file_fk",
                "capability": "populate_backend_ast",
                "payload": {},
            },
            workspace_root=tmp_path,
        )
    )
    tables = {
        item["name"]: item
        for item in artifact["nodes"]
        if item["kind"] == "table"
    }
    assert any(
        edge["relation"] == "references"
        and edge["source_id"] == tables["orders"]["id"]
        and edge["target_id"] == tables["customers"]["id"]
        for edge in artifact["edges"]
    )
    assert artifact["uncertainties"] == []


def test_populate_backend_ast_types_ambiguous_and_missing_sql_fk_uncertainty(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    (tmp_path / "a.sql").write_text(
        "CREATE TABLE customers (id INTEGER PRIMARY KEY);\n", encoding="utf-8"
    )
    (tmp_path / "b.sql").write_text(
        "CREATE TABLE customers (id INTEGER PRIMARY KEY);\n", encoding="utf-8"
    )
    (tmp_path / "orders.sql").write_text(
        "CREATE TABLE orders (\n"
        " customer_id INTEGER REFERENCES customers(id),\n"
        " owner_id INTEGER REFERENCES absent_owners(id)\n"
        ");\n",
        encoding="utf-8",
    )

    artifact = _materialize_graph_v2(
        execute_job(
            {
                "job_id": "job_sql_uncertain_fk",
                "capability": "populate_backend_ast",
                "payload": {},
            },
            workspace_root=tmp_path,
        )
    )
    assert len(artifact["uncertainties"]) == 2
    assert {
        item["reason_code"] for item in artifact["uncertainties"]
    } == {"external_boundary_unresolved"}
    data_access = artifact["graph_contract"]["completeness"]["capabilities"][
        "data_access"
    ]
    assert data_access["status"] == "partial"
    assert data_access["reasons"] == [
        {
            "code": "external_boundary_unresolved",
            "count": 2,
            "language": None,
            "paths_sample": ["orders.sql"],
        }
    ]


def test_populate_backend_ast_preserves_ambiguous_and_missing_orm_fk_uncertainty(
    tmp_path,
):
    from hermes_cli.hades_backend_jobs import execute_job

    (tmp_path / "a.py").write_text(
        "from django.db import models\n"
        "class CustomerDjango(models.Model):\n"
        "    id = models.IntegerField(primary_key=True)\n"
        "    class Meta:\n"
        "        db_table = 'customers'\n",
        encoding="utf-8",
    )
    (tmp_path / "b.py").write_text(
        "from sqlalchemy import Column, Integer\n"
        "class CustomerB:\n"
        "    __tablename__ = 'customers'\n"
        "    id = Column(Integer, primary_key=True)\n",
        encoding="utf-8",
    )
    (tmp_path / "orders.py").write_text(
        "from sqlalchemy import Column, ForeignKey, Integer\n"
        "class Order:\n"
        "    __tablename__ = 'orders'\n"
        "    customer_id = Column(Integer, ForeignKey('customers.id'))\n"
        "    owner_id = Column(Integer, ForeignKey('absent_owners.id'))\n",
        encoding="utf-8",
    )

    result = execute_job(
        {
            "job_id": "job_orm_uncertain_fk",
            "capability": "populate_backend_ast",
            "payload": {"head_commit": "abc123"},
        },
        workspace_root=tmp_path,
    )
    artifact = _materialize_graph_v2(result)

    assert len(artifact["uncertainties"]) == 2
    assert {
        item["reason_code"] for item in artifact["uncertainties"]
    } == {"external_boundary_unresolved"}
    assert {
        item["identity"]["public_resource_name"]
        for item in artifact["nodes"]
        if item["kind"] == "external_boundary"
    } == {"customers", "absent_owners"}
    assert {
        (item["path"], item["reason"])
        for item in result["source_slice_candidates"]
    } >= {("orders.py", "branch_unresolved")}
    data_access = artifact["graph_contract"]["completeness"]["capabilities"][
        "data_access"
    ]
    assert data_access["status"] == "partial"
    assert data_access["reasons"] == [
        {
            "code": "external_boundary_unresolved",
            "count": 2,
            "language": None,
            "paths_sample": ["orders.py"],
        }
    ]


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

    artifact = _materialize_graph_v2(result)
    names = {item["name"] for item in artifact["nodes"]}

    assert "Local" in names
    assert "Outside" not in names
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

    artifact = _materialize_graph_v2(result)
    names = {item["name"] for item in artifact["nodes"]}

    assert result["status"] == "completed"
    assert "Good" in names
    assert "Bad" in names


def test_populate_backend_ast_graph_v2_builds_private_bundle_before_upload(
    monkeypatch, tmp_path
):
    from hermes_cli.hades_backend_jobs import execute_job
    from hermes_cli.hades_graph_v2.bundle import GraphBundleWriter
    from hermes_cli.hades_index.tree_sitter_adapter import TreeSitterAdapter

    workspace = tmp_path / "workspace"
    home = tmp_path / "hermes-home"
    workspace.mkdir()
    (workspace / "requirements.txt").write_text(
        "fastapi==0.115.0\nstarlette==0.37.2\n", encoding="utf-8"
    )
    (workspace / "api.py").write_text(
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n\n"
        "def helper():\n    return 1\n\n"
        "@app.get('/items/{item_id}')\n"
        "def item(item_id: int):\n"
        "    if item_id:\n        return helper()\n"
        "    return 0\n\n"
        "class AdminControllerBulkDeleteBehavior:\n"
        "    def bulk_delete(self):\n"
        "        return helper()\n",
        encoding="utf-8",
    )
    (workspace / "sample.php").write_text(
        "<?php\n"
        "function php_helper() { return 1; }\n"
        "function php_route($value) {\n"
        "  if ($value) { return php_helper(); }\n"
        "  return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    (workspace / "package.json").write_text(
        json.dumps({"dependencies": {"next": "14.2.0"}}), encoding="utf-8"
    )
    route = workspace / "app" / "api" / "status" / "route.ts"
    route.parent.mkdir(parents=True)
    route.write_text(
        "function helper() { return Response.json({ ok: true }) }\n"
        "export async function GET() { return helper() }\n"
        "export const POST = async () => GET()\n",
        encoding="utf-8",
    )
    test_source = workspace / "tests" / "test_api.py"
    test_source.parent.mkdir(parents=True)
    test_source.write_text(
        "def test_item_route():\n    assert True\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HADES_HOME", raising=False)
    parsed_paths = []
    real_parse_file = TreeSitterAdapter.parse_file

    def tracked_parse_file(self, path, *, relative_path, language, max_bytes):
        parsed_paths.append(relative_path)
        return real_parse_file(
            self,
            path,
            relative_path=relative_path,
            language=language,
            max_bytes=max_bytes,
        )

    monkeypatch.setattr(TreeSitterAdapter, "parse_file", tracked_parse_file)

    result = execute_job(
        {
            "job_id": "job_graph_v2",
            "capability": "populate_backend_ast",
            "payload": {
                "project_id": "01KXJD0SV73EBGWKNE2EK3M4KD",
                "workspace_binding_id": "01KXJD1BDMQ2TFABMVJV6EFE8Q",
            },
        },
        workspace_root=workspace,
    )

    artifact = result["artifact"]
    spool = home / "cache" / "hades" / "graph-imports" / (
        "01KXJD0SV73EBGWKNE2EK3M4KD/01KXJD1BDMQ2TFABMVJV6EFE8Q/"
        + artifact["artifact_graph_version"]
    )
    resumed = GraphBundleWriter().resume_state(spool)

    assert result["status"] == "completed"
    assert set(parsed_paths) == {
        "api.py",
        "sample.php",
        "app/api/status/route.ts",
        "tests/test_api.py",
    }
    assert not {"requirements.txt", "package.json"} & set(parsed_paths)
    assert result["source_slice_candidates"]
    assert result["source_slice_candidates"][0]["reason"] == "entrypoint_root"
    assert any(
        candidate["path"] == "tests/test_api.py" and candidate["reason"] == "test"
        for candidate in result["source_slice_candidates"]
    )
    assert artifact["schema"] == "hades.code_graph.v2"
    assert artifact["bundle"]["schema"] == "hades.graph_bundle.v2"
    assert artifact["bundle"] == resumed.manifest
    assert "spool_path" not in artifact
    assert artifact["source_identity"] == artifact["bundle"]["source"]
    families = {}
    for path in resumed.chunk_paths:
        chunk = json.loads(gzip.decompress(path.read_bytes()))
        families.setdefault(chunk["kind"], []).extend(chunk["records"])
    nodes = families["nodes"]
    edges = families["edges"]
    entrypoints = families["entrypoints"]
    assert {node.get("language") for node in nodes} >= {
        "php",
        "python",
        "typescript",
    }
    assert sum(node.get("kind") in {"function", "method"} for node in nodes) >= 7
    assert any(item.get("public_path") == "/api/status" for item in entrypoints)
    assert any(item.get("public_path") == "/items/{item_id}" for item in entrypoints)
    assert any(edge.get("relation") == "invokes" for edge in edges)
    admin = next(
        node
        for node in nodes
        if node.get("kind") == "class"
        and node.get("name") == "AdminControllerBulkDeleteBehavior"
    )
    bulk_delete = next(
        node
        for node in nodes
        if node.get("kind") == "method"
        and node.get("name") == "bulk_delete"
    )
    assert any(
        edge.get("relation") == "contains"
        and edge.get("source_id") == admin["id"]
        and edge.get("target_id") == bulk_delete["id"]
        for edge in edges
    )
    assert any(
        node.get("kind") == "test"
        and node.get("location", {}).get("path") == "tests/test_api.py"
        for node in nodes
    )
    assert any(
        edge.get("relation") in {"passes_through", "routes_to"} for edge in edges
    )
    assert families["flows"]
    assert families["flow_steps"]
    assert resumed.missing_chunk_indexes == tuple(
        range(len(artifact["bundle"]["chunks"]))
    )
    assert not any(key in artifact for key in ("symbols", "routes", "database"))


def test_graph_v2_bounds_metadata_reads_and_reports_typed_partial(monkeypatch, tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    (home / "config.yaml").write_text(
        "hades:\n  graph_index:\n    max_file_bytes: 1024\n",
        encoding="utf-8",
    )
    (workspace / "package.json").write_text(
        json.dumps({
            "dependencies": {"next": "14.2.0"},
            "padding": "x" * 4096,
        }),
        encoding="utf-8",
    )
    route = workspace / "app" / "api" / "status" / "route.ts"
    route.parent.mkdir(parents=True)
    route.write_text(
        "export async function GET() { return Response.json({ ok: true }) }\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))

    result = execute_job(
        {
            "capability": "populate_backend_ast",
            "payload": {
                "project_id": "01KXJD0SV73EBGWKNE2EK3M4KD",
                "workspace_binding_id": "01KXJD1BDMQ2TFABMVJV6EFE8Q",
            },
        },
        workspace_root=workspace,
    )

    assert result["status"] == "completed"
    assert "file_too_large" in json.dumps(result["artifact"]["bundle"])
    assert result["artifact"]["bundle"]["frameworks"] == []


def test_graph_v2_rejects_missing_backend_binding_identity(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    (tmp_path / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="requires project_id and workspace_binding_id"):
        execute_job(
            {"capability": "populate_backend_ast", "payload": {}},
            workspace_root=tmp_path,
        )
