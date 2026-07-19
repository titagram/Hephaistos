"""Native, source-free Laravel side-effect extraction contracts."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from hermes_cli.hades_graph_v2.model import EdgeFlow, NodeKind, Relation
from hermes_cli.hades_graph_v2.validation import GraphValidationError
from hermes_cli.hades_index.lifecycle.assembler import assemble_graph_v2_adapter_result
from hermes_cli.hades_index.lifecycle.builder import GraphBuilder, effect_kind_mapping
from hermes_cli.hades_index.lifecycle.model import (
    CoverageCapability,
    CoverageOutcome,
    EffectKind,
    IRValidationError,
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
        and fact.reason_code == "dynamic_dispatch"
        for fact in result.unresolved_facts
    )
    assert any(
        event.capability is CoverageCapability.DATA_ACCESS
        and event.outcome is CoverageOutcome.PARTIAL
        for event in result.coverage_events
    )


def test_structural_literals_are_laravel_context_only_and_private_values_are_unknown() -> None:
    parsed = TreeSitterAdapter().parse_bytes(
        b"""<?php
use Illuminate\\Support\\Facades\\Cache;
function authenticate($value) { return $value; }
class Orders {
    public function show() {
        authenticate('sk_live_supersecret');
        open('/Users/alice/.ssh/id_rsa');
        read('../../.env');
        Cache::get('orders:recent');
        Cache::get('eyJhbGciOiJIUzI1NiJ9.payload.signature');
        Cache::get('/Users/alice/.ssh/id_rsa');
        Cache::get('../../.env');
    }
}
""",
        path="app/Http/Controllers/Orders.php",
        language="php",
    )

    assert parsed.status == "parsed"
    calls = parsed.syntax.calls  # type: ignore[union-attr]
    assert all(
        call.arguments[0].kind == "unknown"
        for call in calls
        if call.member in {"authenticate", "open", "read"}
    )
    cache_arguments = [
        call.arguments[0]
        for call in calls
        if call.receiver == "Cache" and call.member == "get"
    ]
    assert [item.value for item in cache_arguments] == ["orders:recent", None, None, None]

    non_php = TreeSitterAdapter().parse_bytes(
        b"def show():\n    open('orders:recent')\n",
        path="src/app.py",
        language="python",
    )
    assert non_php.status == "parsed"
    assert all(
        argument.kind == "unknown"
        for call in non_php.syntax.calls  # type: ignore[union-attr]
        for argument in call.arguments
    )


def test_laravel_effects_allow_public_dot_segments_and_reject_sensitive_ones(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    source = """<?php
use Illuminate\\Support\\Facades\\Cache;
use Illuminate\\Support\\Facades\\Http;
use Illuminate\\Support\\Facades\\Storage;
class Orders {
    public function show() {
        Cache::get('.well-known');
        Storage::get('.well-known/acme-challenge');
        Storage::get('.env.example');
        Http::get('https://api.example.test/.well-known/openid-configuration');
        Storage::get('../x');
        Storage::get('.env');
        Storage::get('.ssh/id_rsa');
        Storage::get('.ENV');
        Storage::get('.env.local');
        Storage::get('C:/Users/alice/credentials.txt');
        Storage::get('c:/Users/alice/credentials.txt');
        Storage::get('C:\\Users\\alice\\credentials.txt');
        Storage::get('//server/share/secret.txt');
        Storage::get('\\\\server\\share\\secret.txt');
        Storage::get('file:///Users/alice/credentials.txt');
        Cache::get('.env');
        Cache::get('.ENV.EXAMPLE');
        Http::get('https://api.example.test/.well-known/openid-configuration?token=x');
        Http::get('https://user:pass@api.example.test/path');
        Http::get('https://api.example.test/path#fragment');
        Http::get('https://api.example.test/.env.production');
    }
}
"""
    _write(tmp_path, "app/Http/Controllers/Orders.php", source)
    parsed = TreeSitterAdapter().parse_bytes(
        source.encode(), path="app/Http/Controllers/Orders.php", language="php"
    )

    assert parsed.status == "parsed"
    calls = parsed.syntax.calls  # type: ignore[union-attr]
    retained_literals = {
        argument.value
        for call in calls
        for argument in call.arguments
        if argument.kind == "literal"
    }
    assert {
        ".well-known",
        ".well-known/acme-challenge",
        ".env.example",
        "https://api.example.test/.well-known/openid-configuration",
    } <= retained_literals
    assert not {
        "../x",
        ".env",
        ".ssh/id_rsa",
        ".ENV",
        ".env.local",
        ".ENV.EXAMPLE",
        "C:/Users/alice/credentials.txt",
        "c:/Users/alice/credentials.txt",
        "C:\\Users\\alice\\credentials.txt",
        "//server/share/secret.txt",
        "\\\\server\\share\\secret.txt",
        "file:///Users/alice/credentials.txt",
        "https://api.example.test/.well-known/openid-configuration?token=x",
        "https://user:pass@api.example.test/path",
        "https://api.example.test/path#fragment",
        "https://api.example.test/.env.production",
    } & retained_literals

    result = assemble_graph_v2_adapter_result(_context(tmp_path), (parsed.syntax,))  # type: ignore[arg-type]
    effects = {(effect.kind, effect.public_resource_name) for effect in result.effects}
    assert {
        (EffectKind.CACHE_READ, ".well-known"),
        (EffectKind.STORAGE_READ, ".well-known/acme-challenge"),
        (EffectKind.STORAGE_READ, ".env.example"),
        (
            EffectKind.EXTERNAL_CALL,
            "https://api.example.test/.well-known/openid-configuration",
        ),
    } <= effects
    assert not any(
        resource
        in {
            "../x",
            ".env",
            ".ssh/id_rsa",
            ".ENV",
            ".env.local",
            ".ENV.EXAMPLE",
            "C:/Users/alice/credentials.txt",
            "c:/Users/alice/credentials.txt",
            "C:\\Users\\alice\\credentials.txt",
            "//server/share/secret.txt",
            "\\\\server\\share\\secret.txt",
            "file:///Users/alice/credentials.txt",
            "https://api.example.test/.env.production",
        }
        for _kind, resource in effects
    )
    valid_context = replace(
        _context(tmp_path),
        project_id="01KXJD0SV73EBGWKNE2EK3M4KD",
        workspace_binding_id="01KXJD1BDMQ2TFABMVJV6EFE8Q",
    )
    GraphBuilder(generated_at=lambda: "2026-07-19T12:00:00Z").build(
        valid_context, (result,)
    )


def test_laravel_effects_keep_exact_ownership_chain_and_blocks(tmp_path: Path) -> None:
    _prepare_project(tmp_path)
    source = """<?php
use Illuminate\\Support\\Facades\\DB;
use Illuminate\\Support\\Facades\\Cache;
use App\\Utils\\Cache as AppCache;
use App\\DTO\\User;
class Orders {
    public function show() {
        if (true) { Cache::get('orders'); }
        DB::table('orders')->get(); DB::table('users')->update([]);
        AppCache::get('not-a-cache');
        User::create([]);
        \\App\\Infra\\Storage::get('not-storage');
    }
}
"""
    _write(tmp_path, "app/Http/Controllers/Orders.php", source)
    parsed = TreeSitterAdapter().parse_bytes(
        source.encode(), path="app/Http/Controllers/Orders.php", language="php"
    )

    assert parsed.status == "parsed"
    result = assemble_graph_v2_adapter_result(_context(tmp_path), (parsed.syntax,))  # type: ignore[arg-type]
    effects = {(item.kind, item.operation, item.public_resource_name) for item in result.effects}

    assert (EffectKind.CACHE_READ, "get", "orders") in effects
    assert (EffectKind.DATA_READ, "get", "orders") in effects
    assert (EffectKind.DATA_WRITE, "update", "users") in effects
    assert all(resource not in {"not-a-cache", "User", "not-storage"} for _, _, resource in effects)
    cache_effect = next(item for item in result.effects if item.public_resource_name == "orders")
    assert cache_effect.source.local_key != next(
        item.entry_block_key
        for item in result.declarations
        if item.locator.source_location.path == "app/Http/Controllers/Orders.php"
    )
    assert type(cache_effect.source).__name__ == "BlockEffectSource"
    assert any(
        block.local_key == cache_effect.source.local_key
        and block.locator.structural_path == cache_effect.locator.structural_path
        for block in result.blocks
    )


def test_dynamic_async_effects_are_typed_and_known_async_nodes_are_canonical(tmp_path: Path) -> None:
    _prepare_project(tmp_path)
    event_source = "<?php namespace App\\Events; class OrderPlaced {}\n"
    job_source = "<?php namespace App\\Jobs; class SyncOrder {}\n"
    _write(tmp_path, "app/Events/OrderPlaced.php", event_source)
    _write(tmp_path, "app/Jobs/SyncOrder.php", job_source)
    controller_a = """<?php
use Illuminate\\Support\\Facades\\Event;
use Illuminate\\Support\\Facades\\Bus;
use Illuminate\\Support\\Facades\\Queue;
use App\\Events\\OrderPlaced;
use App\\Jobs\\SyncOrder;
class A { public function show($event, $job) {
    Event::dispatch($event); Bus::dispatch($job); Queue::push($job);
    Event::dispatch(new OrderPlaced()); Bus::dispatch(new SyncOrder()); Queue::push(new SyncOrder());
} }
"""
    controller_b = controller_a.replace("class A", "class B").replace("$event, $job", "$event, $job")
    _write(tmp_path, "app/Http/Controllers/A.php", controller_a)
    _write(tmp_path, "app/Http/Controllers/B.php", controller_b)
    syntax = tuple(
        TreeSitterAdapter().parse_bytes(source.encode(), path=path, language="php").syntax
        for path, source in (
            ("app/Events/OrderPlaced.php", event_source),
            ("app/Jobs/SyncOrder.php", job_source),
            ("app/Http/Controllers/A.php", controller_a),
            ("app/Http/Controllers/B.php", controller_b),
        )
    )

    result = assemble_graph_v2_adapter_result(_context(tmp_path), syntax)  # type: ignore[arg-type]
    assert {fact.resolution_kind for fact in result.unresolved_facts} >= {ResolutionKind.ASYNC_TARGET}
    assert any(event.capability is CoverageCapability.ASYNC for event in result.coverage_events)
    async_effects = [
        effect
        for effect in result.effects
        if effect.kind in {EffectKind.EVENT_EMIT, EffectKind.JOB_DISPATCH, EffectKind.QUEUE_DISPATCH}
    ]
    assert all(effect.target_source_node_local_key is not None for effect in async_effects)
    valid_context = replace(
        _context(tmp_path),
        project_id="01KXJD0SV73EBGWKNE2EK3M4KD",
        workspace_binding_id="01KXJD1BDMQ2TFABMVJV6EFE8Q",
    )
    artifact = GraphBuilder(generated_at=lambda: "2026-07-19T12:00:00Z").build(
        valid_context, (result,)
    )
    assert len([node for node in artifact.nodes if node.kind is NodeKind.EVENT]) == 1
    assert len([node for node in artifact.nodes if node.kind is NodeKind.JOB]) == 1
    assert len([node for node in artifact.nodes if node.kind is NodeKind.QUEUE]) == 1


@pytest.mark.parametrize(
    "private_resource",
    [
        "sk_live_supersecret",
        "eyJhbGciOiJIUzI1NiJ9.payload.signature",
        "access_token:secret",
        "/Users/alice/.ssh/id_rsa",
        "~/.aws/credentials",
        "../../.env",
        ".",
        "..",
        "../x",
        ".env",
        ".env.local",
        ".ENV",
        ".ENV.EXAMPLE",
        ".env.production",
        ".ssh/id_rsa",
        ".git/config",
        ".aws/credentials",
        "https://api.example.test/path?query=1",
        "HTTPS://api.example.test/path?query=1",
        "hTtPs://api.example.test/path#fragment",
        "https://user:pass@api.example.test/path",
        "https://api.example.test/path#fragment",
        "https://api.example.test/.env.production",
        "C:/Users/alice/credentials.txt",
        "c:/Users/alice/credentials.txt",
        r"C:\Users\alice\credentials.txt",
        r"..\x",
        r"safe\.env",
        r"safe\.ENV.LOCAL",
        "//server/share/secret.txt",
        r"\\server\share\secret.txt",
        "file:///Users/alice/credentials.txt",
        "FILE:///Users/alice/credentials.txt",
        "safe\x00resource",
    ],
)
def test_graph_v2_rejects_private_effect_resource_names(tmp_path: Path, private_resource: str) -> None:
    _prepare_project(tmp_path)
    source = """<?php
use Illuminate\\Support\\Facades\\Cache;
class Orders { public function show() { Cache::get('orders'); } }
"""
    _write(tmp_path, "app/Http/Controllers/Orders.php", source)
    parsed = TreeSitterAdapter().parse_bytes(
        source.encode(), path="app/Http/Controllers/Orders.php", language="php"
    )
    result = assemble_graph_v2_adapter_result(_context(tmp_path), (parsed.syntax,))  # type: ignore[arg-type]
    valid_context = replace(
        _context(tmp_path),
        project_id="01KXJD0SV73EBGWKNE2EK3M4KD",
        workspace_binding_id="01KXJD1BDMQ2TFABMVJV6EFE8Q",
    )

    with pytest.raises((GraphValidationError, IRValidationError)):
        poisoned = replace(
            result,
            effects=tuple(
                replace(effect, public_resource_name=private_resource)
                for effect in result.effects
            ),
        )
        GraphBuilder(generated_at=lambda: "2026-07-19T12:00:00Z").build(
            valid_context, (poisoned,)
        )
