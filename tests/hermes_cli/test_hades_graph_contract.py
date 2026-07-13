from copy import deepcopy
import json
from pathlib import Path

from hermes_cli.hades_graph_contract import finalize_graph_artifact


def test_finalize_graph_artifact_records_source_and_quality(tmp_path: Path):
    graph = {
        "schema": "hades.php_graph.v1",
        "language": "php",
        "symbols": [{"id": "class:A", "name": "A", "kind": "class", "path": "a.php"}],
        "edges": [
            {"id": "calls:1", "kind": "calls", "source": "class:A", "target": "class:B"}
        ],
        "truncated": False,
    }
    result = finalize_graph_artifact(
        graph,
        payload={"head_commit": "abc123", "branch": "main"},
        candidates=[tmp_path / "a.php"],
        omitted=[],
    )
    assert result["graph_contract"] == {
        "version": "hades.graph_artifact.v1",
        "extractor": {
            "name": "hades-native-php",
            "version": "1",
            "mode": "native",
            "quality": "full",
            "fallback_reason": None,
        },
        "coverage": {
            "languages": ["php"],
            "files_total": 1,
            "files_analyzed": 1,
            "files_failed": 0,
        },
        "source": {"branch": "main", "head_commit": "abc123"},
    }
    assert result["canonicalization"]["nodes_synthesized"] == 1
    assert result["head_commit"] == "abc123"


def test_finalize_graph_artifact_exposes_inventory_fallback(tmp_path: Path):
    result = finalize_graph_artifact(
        {
            "schema": "hades.code_graph.v1",
            "language": "typescript",
            "symbols": [],
            "edges": [],
            "truncated": True,
        },
        payload={"workspace_head_commit": "def456"},
        candidates=[tmp_path / "app.ts"],
        omitted=[{"path": "large.ts", "reason": "max_file_bytes"}],
    )
    assert result["graph_contract"]["extractor"]["quality"] == "inventory_only"
    assert (
        result["graph_contract"]["extractor"]["fallback_reason"]
        == "no_relationships_extracted"
    )
    assert result["graph_contract"]["coverage"]["files_failed"] == 1


def _finalize(graph: dict, *, max_symbols: int = 5_000) -> dict:
    return finalize_graph_artifact(
        deepcopy(graph),
        payload={
            "head_commit": "abc123",
            "branch": "main",
            "max_graph_nodes": max_symbols,
        },
        candidates=[],
        omitted=[],
    )


def test_canonicalizes_php_like_nodes_and_closes_legacy_edges_without_paths_in_ids():
    local_root = "/Users/alice/Dev/private-project"
    graph = {
        "schema": "hades.php_graph.v1",
        "language": "php",
        "symbols": [
            {
                "kind": "class",
                "name": "App\\Http\\Controllers\\OrderController",
                "namespace": "App\\Http\\Controllers",
                "path": f"{local_root}/app/Http/Controllers/OrderController.php",
            },
            {
                "kind": "method",
                "name": "OrderController@show",
                "class": "App\\Http\\Controllers\\OrderController",
                "method": "show",
                "signature": "show(Order $order)",
                "path": f"{local_root}/app/Http/Controllers/OrderController.php",
            },
            {
                "kind": "route",
                "name": "orders.show",
                "signature": "GET /orders/{order}",
                "path": "routes/web.php",
            },
            {"kind": "file", "path": "app/Http/Controllers/OrderController.php"},
        ],
        "edges": [
            {
                "kind": "declares",
                "from": "App\\Http\\Controllers\\OrderController",
                "to": "OrderController@show",
            },
            {
                "kind": "route_handler",
                "from": "route:orders.show",
                "to": "OrderController@show",
            },
        ],
        "truncated": False,
    }

    result = _finalize(graph)
    node_ids = {node["id"] for node in result["nodes"]}

    assert len(node_ids) == 4
    assert all(node_id.startswith("hades:node:v1:") for node_id in node_ids)
    assert all(local_root not in node_id for node_id in node_ids)
    assert all(
        edge["source_id"] in node_ids and edge["target_id"] in node_ids
        for edge in result["relationships"]
    )
    assert all(
        edge["id"].startswith("hades:edge:v1:") for edge in result["relationships"]
    )
    assert result["canonicalization"]["edges_omitted"] == 0


def test_preserves_unique_explicit_node_identifiers_and_remaps_edge_aliases():
    result = _finalize({
        "schema": "hades.code_graph.v1",
        "language": "typescript",
        "symbols": [
            {"id": "class:Order", "kind": "class", "name": "Order"},
            {"symbol_id": "method:Order.save", "kind": "method", "name": "Order.save"},
        ],
        "edges": [{"kind": "calls", "source": "Order", "target": "Order.save"}],
    })

    assert [node["id"] for node in result["nodes"]] == [
        "class:Order",
        "method:Order.save",
    ]
    assert result["symbols"][1] == {
        "symbol_id": "method:Order.save",
        "kind": "method",
        "name": "Order.save",
    }
    assert result["relationships"][0]["source_id"] == "class:Order"
    assert result["relationships"][0]["target_id"] == "method:Order.save"
    assert result["edges"] == [
        {"kind": "calls", "source": "Order", "target": "Order.save"}
    ]


def test_preserves_same_explicit_id_repeated_on_one_node():
    result = _finalize({
        "schema": "hades.code_graph.v1",
        "language": "typescript",
        "symbols": [
            {
                "id": "symbol:Order",
                "symbol_id": "symbol:Order",
                "kind": "class",
                "name": "Order",
            }
        ],
        "edges": [],
    })

    assert result["nodes"][0]["id"] == "symbol:Order"


def test_canonical_ids_and_capacity_selection_are_permutation_invariant():
    nodes = [
        {"kind": "class", "name": "A", "path": "src/A.php"},
        {"kind": "class", "name": "B", "path": "src/B.php"},
        {
            "kind": "method",
            "name": "B@run",
            "class": "B",
            "method": "run",
            "path": "src/B.php",
        },
    ]
    edges = [
        {"kind": "uses", "from": "A", "to": "Vendor\\External"},
        {"kind": "calls", "from": "A", "to": "B@run"},
    ]
    graph = {
        "schema": "hades.php_graph.v1",
        "language": "php",
        "symbols": nodes,
        "edges": edges,
    }
    reversed_graph = {
        **graph,
        "symbols": list(reversed(nodes)),
        "edges": list(reversed(edges)),
    }

    first = _finalize(graph, max_symbols=3)
    second = _finalize(reversed_graph, max_symbols=3)

    assert sorted(
        (node["id"], node.get("name"), node.get("external", False))
        for node in first["nodes"]
    ) == sorted(
        (node["id"], node.get("name"), node.get("external", False))
        for node in second["nodes"]
    )
    assert sorted(
        (edge["id"], edge["source_id"], edge["target_id"])
        for edge in first["relationships"]
    ) == sorted(
        (edge["id"], edge["source_id"], edge["target_id"])
        for edge in second["relationships"]
    )


def test_ambiguous_alias_is_not_resolved_by_input_order():
    graph = {
        "schema": "hades.php_graph.v1",
        "language": "php",
        "symbols": [
            {
                "kind": "method",
                "name": "Worker@run",
                "class": "App\\A\\Worker",
                "method": "run",
                "path": "a.php",
            },
            {
                "kind": "method",
                "name": "Worker@run",
                "class": "App\\B\\Worker",
                "method": "run",
                "path": "b.php",
            },
            {"kind": "class", "name": "Target", "path": "target.php"},
        ],
        "edges": [{"kind": "calls", "from": "Worker@run", "to": "Target"}],
    }

    result = _finalize(graph)
    report = result["canonicalization"]

    assert result["relationships"] == []
    assert report["ambiguous_aliases"] >= 1
    assert report["edges_omitted"] == 1
    assert any(
        issue["reason"] == "ambiguous_endpoint_alias" for issue in report["issues"]
    )
    assert result["graph_contract"]["extractor"]["quality"] == "inventory_only"


def test_distinct_edge_occurrences_get_stable_distinct_ids():
    graph = {
        "schema": "hades.php_graph.v1",
        "language": "php",
        "symbols": [
            {"kind": "method", "name": "A@run", "path": "a.php"},
            {"kind": "class", "name": "B", "path": "b.php"},
        ],
        "edges": [
            {"kind": "calls", "from": "A@run", "to": "B", "path": "a.php", "line": 10},
            {"kind": "calls", "from": "A@run", "to": "B", "path": "a.php", "line": 20},
        ],
    }

    first = _finalize(graph)
    second = _finalize({**graph, "edges": list(reversed(graph["edges"]))})

    assert len(first["relationships"]) == 2
    assert len({edge["id"] for edge in first["relationships"]}) == 2
    assert sorted(edge["id"] for edge in first["relationships"]) == sorted(
        edge["id"] for edge in second["relationships"]
    )


def test_synthesizes_external_endpoint_nodes_but_never_for_ambiguous_aliases():
    result = _finalize({
        "schema": "hades.php_graph.v1",
        "language": "php",
        "symbols": [
            {"kind": "class", "name": "App\\Service", "path": "app/Service.php"}
        ],
        "edges": [
            {"kind": "extends", "from": "App\\Service", "to": "Vendor\\BaseService"},
            {"kind": "query_table", "from": "App\\Service", "to": "table:orders"},
        ],
    })
    external = [node for node in result["nodes"] if node.get("external")]
    node_ids = {node["id"] for node in result["nodes"]}

    assert {node["name"] for node in external} == {
        "Vendor\\BaseService",
        "table:orders",
    }
    assert all(
        edge["source_id"] in node_ids and edge["target_id"] in node_ids
        for edge in result["relationships"]
    )
    report = result["canonicalization"]
    assert report["nodes_synthesized"] == 2
    assert report["endpoint_aliases_missing_before_synthesis"] == 2
    assert report["endpoint_aliases_synthesized"] == 2
    assert report["endpoint_aliases_unresolved"] == 0


def test_missing_node_identity_and_capacity_omissions_are_bounded_and_visible():
    result = _finalize(
        {
            "schema": "hades.code_graph.v1",
            "language": "python",
            "symbols": [
                {"kind": "unknown"},
                {"kind": "class", "name": "A", "path": "a.py"},
                {"kind": "class", "name": "Unused", "path": "unused.py"},
            ],
            "edges": [{"kind": "uses", "from": "A", "to": "Vendor\\External"}],
        },
        max_symbols=2,
    )
    report = result["canonicalization"]

    assert len(result["nodes"]) == 2
    assert report["nodes_input"] == 3
    assert report["nodes_omitted"] == 2
    assert report["nodes_synthesized"] == 1
    assert report["issues_count"] >= 2
    assert len(report["issues"]) <= 50
    assert result["graph_contract"]["extractor"]["quality"] == "partial"
    assert (
        result["graph_contract"]["extractor"]["fallback_reason"]
        == "canonicalization_omissions"
    )


def test_canonicalization_stays_bounded_at_five_thousand_nodes_with_closed_edges():
    nodes = [
        {"kind": "class", "name": f"Class{index}", "path": f"src/Class{index}.php"}
        for index in range(5_000)
    ]
    edges = [
        {"kind": "uses", "from": f"Class{index}", "to": "Vendor\\Shared"}
        for index in range(4_999)
    ]

    result = _finalize({
        "schema": "hades.php_graph.v1",
        "language": "php",
        "symbols": nodes,
        "edges": edges,
        "truncated": True,
    })
    node_ids = {node["id"] for node in result["nodes"]}

    assert len(result["nodes"]) == 5_000
    assert len(result["relationships"]) == 4_999
    assert all("src/Class" not in node["id"] for node in result["nodes"])
    assert all(
        edge["source_id"] in node_ids and edge["target_id"] in node_ids
        for edge in result["relationships"]
    )
    report = result["canonicalization"]
    assert report["nodes_input"] == 5_000
    assert report["nodes_emitted"] == 5_000
    assert report["nodes_synthesized"] == 1
    assert report["nodes_omitted"] == 1
    assert report["edges_input"] == 4_999
    assert report["edges_emitted"] == 4_999
    assert report["edges_omitted"] == 0


def test_reserved_generated_edge_id_cannot_hijack_another_edge_in_any_order():
    nodes = [
        {"id": "class:A", "kind": "class", "name": "A"},
        {"id": "class:B", "kind": "class", "name": "B"},
        {"id": "class:C", "kind": "class", "name": "C"},
    ]
    genuine = {"kind": "calls", "source": "A", "target": "B"}
    derived = _finalize({"symbols": nodes, "edges": [genuine]})["relationships"][0][
        "id"
    ]
    malicious = {
        "id": derived,
        "kind": "calls",
        "source": "A",
        "target": "C",
    }

    for edges in ([genuine, malicious], [malicious, genuine]):
        result = _finalize({"symbols": nodes, "edges": edges})
        assert [edge["id"] for edge in result["relationships"]] == [derived]
        report = result["canonicalization"]
        assert report["edges_omitted"] == 1
        assert report["issue_reasons"]["invalid_reserved_edge_id"] == 1


def test_distinct_edges_with_same_explicit_id_omit_entire_collision_group():
    nodes = [
        {"id": "class:A", "kind": "class", "name": "A"},
        {"id": "class:B", "kind": "class", "name": "B"},
        {"id": "class:C", "kind": "class", "name": "C"},
    ]
    edges = [
        {"id": "edge:shared", "kind": "calls", "source": "A", "target": "B"},
        {"id": "edge:shared", "kind": "calls", "source": "A", "target": "C"},
    ]

    for ordered in (edges, list(reversed(edges))):
        result = _finalize({"symbols": nodes, "edges": ordered})
        assert result["relationships"] == []
        report = result["canonicalization"]
        assert report["edges_omitted"] == 2
        assert report["edges_deduplicated"] == 0
        assert report["issue_reasons"]["edge_id_collision"] == 2


def test_exact_duplicates_are_reported_but_do_not_degrade_graph_quality():
    node = {"id": "class:A", "kind": "class", "name": "A"}
    edge = {"id": "edge:self", "kind": "calls", "source": "A", "target": "A"}
    result = _finalize({
        "symbols": [node, deepcopy(node)],
        "edges": [edge, deepcopy(edge)],
    })

    report = result["canonicalization"]
    assert len(result["nodes"]) == 1
    assert len(result["relationships"]) == 1
    assert report["nodes_deduplicated"] == 1
    assert report["edges_deduplicated"] == 1
    assert report["nodes_omitted"] == 0
    assert report["edges_omitted"] == 0
    assert result["graph_contract"]["extractor"] == {
        "name": "hades-native-unknown",
        "version": "1",
        "mode": "native",
        "quality": "full",
        "fallback_reason": None,
    }


def test_edge_inputs_all_omitted_use_canonicalization_fallback_not_no_edges():
    result = _finalize({
        "symbols": [{"id": "class:A", "kind": "class", "name": "A"}],
        "edges": [{"kind": "calls", "source": "A"}],
    })

    assert result["relationships"] == []
    assert result["canonicalization"]["edges_input"] == 1
    assert result["canonicalization"]["edges_omitted"] == 1
    assert result["graph_contract"]["extractor"]["quality"] == "inventory_only"
    assert (
        result["graph_contract"]["extractor"]["fallback_reason"]
        == "canonicalization_omissions"
    )


def test_rejects_path_like_control_and_oversize_explicit_node_and_edge_ids():
    unsafe_ids = [
        "",
        " edge:spaced ",
        "/etc/passwd",
        "file:///home/alice/private.py",
        r"C:\Users\alice\private.py",
        r"\\server\share\secret.py",
        "node:\x00hidden",
        "x" * 513,
    ]
    for unsafe_id in unsafe_ids:
        result = _finalize({
            "symbols": [
                {"id": unsafe_id, "kind": "class", "name": "A"},
                {"id": "class:B", "kind": "class", "name": "B"},
            ],
            "edges": [
                {
                    "id": unsafe_id,
                    "kind": "calls",
                    "source": "B",
                    "target": "B",
                }
            ],
        })
        report = result["canonicalization"]
        assert all(node["id"] != unsafe_id for node in result["nodes"])
        assert result["relationships"] == []
        assert report["nodes_omitted"] == 1
        assert report["edges_omitted"] == 1
        assert report["issue_reasons"]["invalid_node_id"] == 1
        assert report["issue_reasons"]["invalid_edge_id"] == 1


def test_oversize_or_unrecognized_endpoint_is_omitted_without_raw_placeholder():
    huge = "External" + "X" * 100_000
    original_edges = [
        {"kind": "calls", "source": "A", "target": huge},
        {"kind": "calls", "source": "A", "target": "not a semantic locator"},
    ]
    graph = {
        "symbols": [{"id": "class:A", "kind": "class", "name": "A"}],
        "edges": original_edges,
    }
    result = _finalize(graph)

    assert result["edges"] == original_edges
    assert result["relationships"] == []
    assert not any(node.get("external") for node in result["nodes"])
    report = result["canonicalization"]
    assert report["edges_input"] == 2
    assert report["edges_omitted"] == 2
    assert report["issue_reasons"]["endpoint_locator_too_large"] == 1
    assert report["issue_reasons"]["unrecognized_endpoint_locator"] == 1
    assert huge not in json.dumps(report)
    # The preserved legacy evidence contains the input once; canonicalization
    # adds only bounded hashes/counters, never another raw 100 kB placeholder.
    assert len(json.dumps(result)) < len(json.dumps(graph)) + 20_000


def test_bounded_bare_identifier_is_a_safe_external_symbol_locator():
    result = _finalize({
        "symbols": [{"id": "class:A", "kind": "class", "name": "A"}],
        "edges": [{"kind": "calls", "source": "A", "target": "ExternalService"}],
    })

    external = [node for node in result["nodes"] if node.get("external")]
    assert len(external) == 1
    assert external[0]["kind"] == "external_symbol"
    assert external[0]["name"] == "ExternalService"
    assert result["canonicalization"]["edges_omitted"] == 0
    assert len(result["relationships"]) == 1


def test_structured_route_and_table_locators_allow_only_bounded_semantic_grammar():
    safe = _finalize({
        "symbols": [{"id": "class:A", "kind": "class", "name": "A"}],
        "edges": [
            {"kind": "routes", "source": "A", "target": "route:GET /orders/{id}"},
            {"kind": "queries", "source": "A", "target": "table:shop.orders"},
        ],
    })
    assert len(safe["relationships"]) == 2
    assert {node["kind"] for node in safe["nodes"] if node.get("external")} == {
        "route",
        "table",
    }

    unsafe = _finalize({
        "symbols": [{"id": "class:A", "kind": "class", "name": "A"}],
        "edges": [
            {"kind": "routes", "source": "A", "target": "route:GET  /orders"},
            {"kind": "routes", "source": "A", "target": "route:GET /orders;DROP"},
            {"kind": "queries", "source": "A", "target": "table:{$prefix} orders"},
        ],
    })
    assert unsafe["relationships"] == []
    assert not any(node.get("external") for node in unsafe["nodes"])
    assert unsafe["canonicalization"]["edges_omitted"] == 3
    assert (
        unsafe["canonicalization"]["issue_reasons"]["unrecognized_endpoint_locator"]
        == 3
    )


def test_synthetic_path_placeholder_is_bounded_and_never_exposes_absolute_path():
    for locator in ("src/domain/Order.py", "/home/alice/private/Order.py"):
        result = _finalize({
            "symbols": [{"id": "class:A", "kind": "class", "name": "A"}],
            "edges": [{"kind": "imports", "source": "A", "target": locator}],
        })
        external = [node for node in result["nodes"] if node.get("external")]
        if locator.startswith("/"):
            assert external == []
            assert result["relationships"] == []
            assert (
                result["canonicalization"]["issue_reasons"]["unsafe_endpoint_locator"]
                == 1
            )
        else:
            assert len(external) == 1
            assert external[0]["name"] == "Order.py"
            assert external[0]["path"] == "src/domain/Order.py"
            assert len(json.dumps(external[0]).encode()) < 1_024


def test_preserves_legacy_lists_exactly_including_invalid_shapes_and_counts_them():
    symbols = [
        {"id": "class:A", "kind": "class", "name": "A"},
        "bad-node",
        17,
        None,
    ]
    edges = [
        {"id": "edge:self", "kind": "calls", "source": "A", "target": "A"},
        "bad-edge",
        ["also", "bad"],
    ]
    graph = {"symbols": symbols, "edges": edges}
    expected_json = json.dumps(graph, ensure_ascii=False, separators=(",", ":"))
    result = _finalize(graph)

    assert (
        json.dumps(
            {"symbols": result["symbols"], "edges": result["edges"]},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        == expected_json
    )
    report = result["canonicalization"]
    assert report["nodes_input"] == 4
    assert report["edges_input"] == 3
    assert report["nodes_omitted"] == 3
    assert report["edges_omitted"] == 2
    assert report["issue_reasons"]["invalid_node_shape"] == 3
    assert report["issue_reasons"]["invalid_edge_shape"] == 2


def test_more_than_five_thousand_inputs_are_bounded_without_dropping_legacy_evidence():
    symbols = [
        {"kind": "class", "name": f"Class{index}", "path": f"src/{index}.py"}
        for index in range(5_250)
    ]
    result = _finalize({"symbols": symbols, "edges": []})

    assert len(result["symbols"]) == 5_250
    assert len(result["nodes"]) == 5_000
    report = result["canonicalization"]
    assert report["nodes_input"] == 5_250
    assert report["nodes_emitted"] == 5_000
    assert report["nodes_omitted"] == 250
    assert report["issues_count"] == 250
    assert len(report["issues"]) == 50
    assert report["issues_truncated"] is True
