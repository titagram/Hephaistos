"""Native, source-free Laravel side-effect extraction contracts."""

from __future__ import annotations

from pathlib import Path

from hermes_cli.hades_graph_v2.model import EdgeFlow, NodeKind, Relation
from hermes_cli.hades_index.lifecycle.assembler import assemble_graph_v2_adapter_result
from hermes_cli.hades_index.lifecycle.builder import effect_kind_mapping
from hermes_cli.hades_index.lifecycle.model import (
    CoverageCapability,
    CoverageOutcome,
    EffectKind,
    ResolutionKind,
)
from hermes_cli.hades_index.tree_sitter_adapter import TreeSitterAdapter
from tests.hermes_cli.test_hades_lifecycle_laravel import (
    _context,
    _prepare_project,
    _write,
)


def test_php_structural_calls_preserve_only_safe_effect_facts() -> None:
    parsed = TreeSitterAdapter().parse_bytes(
        b"""<?php
use Illuminate\\Support\\Facades\\Cache as CacheStore;
class Orders {
    public function show() {
        CacheStore::get('orders:recent');
        CacheStore::put('orders:recent', 'payload-secret');
        Http::get('https://api.example.test/orders?token=secret');
        event(new OrderPlaced());
    }
}
""",
        path="app/Http/Controllers/Orders.php",
        language="php",
    )

    assert parsed.status == "parsed"
    calls = parsed.syntax.calls  # type: ignore[union-attr]
    cache = next(call for call in calls if call.member == "get")
    cache_write = next(call for call in calls if call.member == "put")
    http = next(
        call for call in calls if call.member == "get" and call.receiver == "Http"
    )
    event = next(call for call in calls if call.member == "event")

    assert cache.call_form == "scoped"
    assert cache.receiver == "CacheStore"
    assert cache.structural_path.startswith("root/")
    assert cache.arguments[0].kind == "literal"
    assert cache.arguments[0].value == "orders:recent"
    assert cache_write.arguments[1].kind == "unknown"
    assert http.arguments[0].kind == "unknown"
    assert event.arguments[0].kind == "class_reference"
    assert event.arguments[0].value == "OrderPlaced"


def test_effect_kind_mapping_keeps_data_writes_as_query_relations() -> None:
    expected = {
        EffectKind.DATA_READ: (NodeKind.QUERY, Relation.READS, EdgeFlow.ALWAYS),
        EffectKind.DATA_WRITE: (NodeKind.QUERY, Relation.WRITES, EdgeFlow.ALWAYS),
        EffectKind.CACHE_READ: (NodeKind.CACHE, Relation.READS, EdgeFlow.ALWAYS),
        EffectKind.CACHE_WRITE: (NodeKind.CACHE, Relation.WRITES, EdgeFlow.ALWAYS),
        EffectKind.STORAGE_READ: (NodeKind.STORAGE, Relation.READS, EdgeFlow.ALWAYS),
        EffectKind.STORAGE_WRITE: (NodeKind.STORAGE, Relation.WRITES, EdgeFlow.ALWAYS),
        EffectKind.EXTERNAL_CALL: (
            NodeKind.EXTERNAL_BOUNDARY,
            Relation.CALLS_EXTERNAL,
            EdgeFlow.ALWAYS,
        ),
        EffectKind.EVENT_EMIT: (NodeKind.EVENT, Relation.EMITS, EdgeFlow.ASYNC),
        EffectKind.JOB_DISPATCH: (NodeKind.JOB, Relation.DISPATCHES, EdgeFlow.ASYNC),
        EffectKind.QUEUE_DISPATCH: (
            NodeKind.QUEUE,
            Relation.DISPATCHES,
            EdgeFlow.ASYNC,
        ),
    }

    assert {kind: effect_kind_mapping(kind) for kind in EffectKind} == expected


def test_laravel_effects_are_native_and_keep_unsafe_http_private(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(tmp_path, "app/Events/OrderPlaced.php", "<?php class OrderPlaced {}\n")
    _write(tmp_path, "app/Jobs/SyncOrder.php", "<?php class SyncOrder {}\n")
    source = """<?php
use Illuminate\\Support\\Facades\\Cache as CacheStore;
use Illuminate\\Support\\Facades\\DB;
use Illuminate\\Support\\Facades\\Http;
use Illuminate\\Support\\Facades\\Storage;
use Illuminate\\Support\\Facades\\Queue;
class Orders {
    public function show() {
        DB::table('orders')->get();
        DB::table('orders')->update(['state' => 'paid']);
        CacheStore::get('orders:recent');
        CacheStore::put('orders:recent', 'redacted');
        Storage::get('orders/export.csv');
        Storage::put('orders/export.csv', 'redacted');
        Http::get('https://api.example.test/orders?token=secret');
        event(new OrderPlaced());
        dispatch(new SyncOrder());
        Queue::push(new SyncOrder());
    }
}
"""
    _write(tmp_path, "app/Http/Controllers/Orders.php", source)
    parsed = TreeSitterAdapter().parse_bytes(
        source.encode(), path="app/Http/Controllers/Orders.php", language="php"
    )
    event = TreeSitterAdapter().parse_bytes(
        b"<?php class OrderPlaced {}\n",
        path="app/Events/OrderPlaced.php",
        language="php",
    )
    job = TreeSitterAdapter().parse_bytes(
        b"<?php class SyncOrder {}\n", path="app/Jobs/SyncOrder.php", language="php"
    )

    assert parsed.status == "parsed"
    assert event.status == "parsed"
    assert job.status == "parsed"
    result = assemble_graph_v2_adapter_result(  # type: ignore[arg-type]
        _context(tmp_path), (parsed.syntax, event.syntax, job.syntax)
    )
    effects = {
        (effect.kind, effect.operation, effect.public_resource_name)
        for effect in result.effects
    }

    assert {
        (EffectKind.DATA_READ, "get", "orders"),
        (EffectKind.DATA_WRITE, "update", "orders"),
        (EffectKind.CACHE_READ, "get", "orders:recent"),
        (EffectKind.CACHE_WRITE, "put", "orders:recent"),
        (EffectKind.STORAGE_READ, "get", "orders/export.csv"),
        (EffectKind.STORAGE_WRITE, "put", "orders/export.csv"),
        (EffectKind.EXTERNAL_CALL, "get", "http"),
        (EffectKind.EVENT_EMIT, "event", "OrderPlaced"),
        (EffectKind.JOB_DISPATCH, "dispatch", "SyncOrder"),
        (EffectKind.QUEUE_DISPATCH, "push", "SyncOrder"),
    } <= effects
    assert "token=secret" not in str(result)
    assert "redacted" not in str(result)


def test_dynamic_laravel_effect_target_is_explicitly_partial(tmp_path: Path) -> None:
    _prepare_project(tmp_path)
    source = """<?php
use Illuminate\\Support\\Facades\\Cache;
class Orders {
    public function show($key) {
        Cache::get($key);
    }
}
"""
    _write(tmp_path, "app/Http/Controllers/Orders.php", source)
    parsed = TreeSitterAdapter().parse_bytes(
        source.encode(), path="app/Http/Controllers/Orders.php", language="php"
    )

    assert parsed.status == "parsed"
    result = assemble_graph_v2_adapter_result(_context(tmp_path), (parsed.syntax,))  # type: ignore[arg-type]

    assert result.effects == ()
    assert any(
        fact.resolution_kind is ResolutionKind.EXTERNAL_TARGET
        and fact.reason_code == "laravel_effect_resource_unresolved"
        for fact in result.unresolved_facts
    )
    assert any(
        event.capability is CoverageCapability.DATA_ACCESS
        and event.outcome is CoverageOutcome.PARTIAL
        and event.reason_code == "laravel_effect_resource_unresolved"
        for event in result.coverage_events
    )
