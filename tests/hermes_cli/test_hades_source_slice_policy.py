from __future__ import annotations

from pathlib import Path


def _write(root: Path, path: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(f"line {line}" for line in range(1, 80)), encoding="utf-8")


def _node(node_id: str, kind: str, path: str, line: int, **extra):
    return {
        "id": node_id,
        "kind": kind,
        "name": node_id,
        "qualified_name": node_id,
        "location": {"path": path, "start_line": line, "end_line": line + 2},
        **extra,
    }


def test_graph_v2_policy_uses_lifecycle_stage_priority(tmp_path: Path):
    from hermes_cli.hades_source_slice_policy import plan_source_slice_candidates

    paths = [
        "src/route.py",
        "src/auth.py",
        "src/branch.py",
        "src/unresolved.py",
        "src/orders.py",
        "tests/test_orders.py",
    ]
    for path in paths:
        _write(tmp_path, path)

    graph = {
        "schema": "hades.code_graph.v2",
        "entrypoints": [
            {
                "id": "ep-orders",
                "label": "GET /orders",
                "public_path": "/orders",
                "handler_node_id": "handler",
                "registration_occurrence": {
                    "kind": "ast",
                    "path": "src/route.py",
                    "structural_path": "decorator/0",
                    "ordinal": 0,
                },
            }
        ],
        "nodes": [
            _node("handler", "function", "src/route.py", 12),
            _node("auth", "authorization", "src/auth.py", 20),
            _node("branch", "branch", "src/branch.py", 30),
            _node("orders", "domain", "src/orders.py", 40),
            _node("orders-test", "function", "tests/test_orders.py", 50),
        ],
        "uncertainties": [
            {
                "id": "uncertain-branch",
                "source_refs": [{"path": "src/unresolved.py", "line": 31}],
            }
        ],
    }

    candidates = plan_source_slice_candidates(
        tmp_path, graph, head_commit="abc123", max_candidates=20
    )

    assert [candidate["path"] for candidate in candidates] == paths
    assert [candidate["reason"] for candidate in candidates] == [
        "entrypoint_root",
        "middleware_security_input",
        "branch_unresolved",
        "branch_unresolved",
        "domain_data_integration",
        "test",
    ]
    assert [candidate["priority"] for candidate in candidates] == [10, 20, 30, 30, 40, 50]
    assert all(candidate["raw_source_included"] is False for candidate in candidates)
    assert all(candidate["head_commit"] == "abc123" for candidate in candidates)


def test_policy_rejects_graph_v1_instead_of_falling_back_to_legacy_roles(tmp_path: Path):
    from hermes_cli.hades_source_slice_policy import plan_source_slice_candidates

    _write(tmp_path, "app/Models/User.php")
    graph = {
        "schema": "hades.php_graph.v1",
        "symbols": [
            {
                "name": "User",
                "path": "app/Models/User.php",
                "line": 1,
                "role": "eloquent_model",
            }
        ],
    }

    assert plan_source_slice_candidates(tmp_path, graph, head_commit="abc123") == []


def test_graph_v2_policy_rejects_sensitive_and_vendor_paths(tmp_path: Path):
    from hermes_cli.hades_source_slice_policy import plan_source_slice_candidates

    for path in (".env", "vendor/pkg/Secret.php", "src/model.py"):
        _write(tmp_path, path)
    graph = {
        "schema": "hades.code_graph.v2",
        "entrypoints": [],
        "nodes": [
            _node("env", "domain", ".env", 1),
            _node("vendor", "domain", "vendor/pkg/Secret.php", 1),
            _node("model", "model", "src/model.py", 1),
        ],
        "uncertainties": [],
    }

    candidates = plan_source_slice_candidates(tmp_path, graph, head_commit="abc123")

    assert [candidate["path"] for candidate in candidates] == ["src/model.py"]
