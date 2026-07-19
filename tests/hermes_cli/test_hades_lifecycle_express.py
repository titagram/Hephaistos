"""Golden behaviour for static, bounded Express lifecycle extraction."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from hermes_cli.hades_graph_config import load_hades_graph_index_config
from hermes_cli.hades_graph_v2.model import (
    EntrypointKind,
    FrameworkKnowledge,
    FrameworkRecord,
    MethodSemantics,
    SourceIdentity,
)
from hermes_cli.hades_index.lifecycle.model import (
    ConfigLocatorIR,
    ExtractionContext,
    FrameworkLocalTarget,
    SourceLocationIR,
    local_record_key,
)
from hermes_cli.hades_index.tree_sitter_adapter import (
    ParsedFile,
    StructuralSymbol,
    SyntaxIR,
)


def _write(root: Path, content: str) -> None:
    (root / "app.js").write_text(content, encoding="utf-8")
    (root / "package.json").write_text(
        '{"dependencies":{"express":"5.2.1"}}', encoding="utf-8"
    )


def _location(root: Path, path: str) -> SourceLocationIR:
    content = (root / path).read_bytes()
    return SourceLocationIR(
        path, 1, max(1, content.count(b"\n") + 1), hashlib.sha256(content).hexdigest()
    )


def _context(root: Path) -> ExtractionContext:
    metadata = _location(root, "package.json")
    return ExtractionContext(
        workspace_root=root,
        project_id="project",
        workspace_binding_id="binding",
        source_identity=SourceIdentity(None, "a" * 64, False, None),
        graph_config=load_hades_graph_index_config({}),
        detected_languages=("javascript",),
        detected_frameworks=(
            FrameworkRecord(
                language="javascript",
                name="express",
                version="5.2.1",
                detector="package_json",
                configuration_paths=("package.json",),
                knowledge=FrameworkKnowledge.VERIFIED,
            ),
        ),
        composer_metadata=(),
        python_metadata=(),
        package_metadata=(ConfigLocatorIR(metadata, "package_json", 0),),
        tsconfig_metadata=(),
        file_accessor=lambda path: (root / path).read_bytes(),
    )


def _syntax(root: Path) -> SyntaxIR:
    source = (root / "app.js").read_text(encoding="utf-8")
    symbols = tuple(
        StructuralSymbol(
            match.group("name"),
            "function",
            source.count("\n", 0, match.start()) + 1,
            source.count("\n", 0, match.start()) + 1,
        )
        for match in re.finditer(
            r"(?:async\s+)?function\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*\(", source
        )
    )
    return SyntaxIR(ParsedFile("app.js", "javascript", symbols, (), ()), ())


def _adapter(root: Path):
    from hermes_cli.hades_index.lifecycle.frameworks.express import (
        ExpressLifecycleAdapter,
    )

    return ExpressLifecycleAdapter(), _context(root), _syntax(root)


def _entries(root: Path):
    adapter, context, syntax = _adapter(root)
    entries = adapter.entrypoints(context, (syntax,))
    return adapter, context, entries


def _roles(adapter, context, entry):
    return [segment.framework_role for segment in adapter.pipeline(context, entry)]


def _coverage(adapter, context):
    return {event.reason_code for event in adapter.coverage_events(context)}


def _function_key(root: Path, name: str) -> str:
    syntax = _syntax(root)
    return next(
        local_record_key(
            "javascript",
            syntax.path,
            "executable_declaration",
            "ast",
            f"symbol/{symbol.name}",
            ordinal,
        )
        for ordinal, symbol in enumerate(syntax.symbols)
        if symbol.name == name
    )


def test_nested_router_mount_composes_literal_path_prefix_exactly(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        """
const express = require('express');
const app = express(); const parent = express.Router(); const child = express.Router();
function childHandler(req, res) { res.send('ok'); }
child.get('/items/:id', childHandler); parent.use('/v1', child); app.use('/api', parent);
""",
    )
    adapter, context, entries = _entries(tmp_path)
    route = next(item for item in entries if item.public_path == "/api/v1/items/:id")
    assert route.kind is EntrypointKind.HTTP_ROUTE
    assert route.methods == ("GET",)
    assert route.handler_local_key is not None
    pipeline = adapter.pipeline(context, route)
    assert [segment.framework_role for segment in pipeline] == [
        "router_mount",
        "router_mount",
        "route_handler",
        "terminal_send",
    ]
    assert [segment.target.descriptor.public_name for segment in pipeline[:2]] == [
        "app.js:parent@/api",
        "app.js:child@/api/v1",
    ]
    assert route.framework_segment_keys == tuple(
        segment.local_key for segment in pipeline
    )


def test_computed_router_mount_is_partial_with_explicit_mount_uncertainty(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        """
const express = require('express'); const app = express(); const child = express.Router();
function handler(req,res) { res.end(); } child.get('/items/:id', handler); app.use(prefix, child);
""",
    )
    adapter, context, entries = _entries(tmp_path)
    assert {"mount_prefix_unresolved", "router_target_unresolved"} <= _coverage(
        adapter, context
    )
    assert not entries


def test_same_path_explicit_verbs_remain_distinct_and_ordered(tmp_path: Path) -> None:
    _write(
        tmp_path,
        """
const express = require('express'); const app = express();
function getHandler() {} function postHandler() {}
app.get('/same', getHandler); app.post('/same', postHandler);
""",
    )
    _, _, entries = _entries(tmp_path)
    routes = [item for item in entries if item.public_path == "/same"]
    assert [item.methods for item in routes] == [("GET",), ("POST",)]
    assert [item.public_name for item in routes] == ["getHandler", "postHandler"]


def test_computed_verb_registration_is_unresolved_without_an_invented_method(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        """
const express = require('express'); const app = express(); function handler() {}
app[verb]('/same', handler);
""",
    )
    adapter, context, entries = _entries(tmp_path)
    assert not any(item.public_path == "/same" for item in entries)
    assert "route_method_unresolved" in _coverage(adapter, context)


def test_all_registration_remains_method_unrestricted(tmp_path: Path) -> None:
    _write(
        tmp_path,
        """
const express = require('express'); const app = express(); function handler() {}
app.all('/all', handler);
""",
    )
    _, _, entries = _entries(tmp_path)
    route = next(item for item in entries if item.public_path == "/all")
    assert route.method_semantics is MethodSemantics.UNRESTRICTED
    assert route.methods == ()
    assert route.public_name == "handler"


def test_all_with_computed_path_is_partial_without_an_invented_path(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        """
const express = require('express'); const app = express(); function handler() {}
app.all(path, handler);
""",
    )
    adapter, context, entries = _entries(tmp_path)
    assert not entries
    assert "route_path_unresolved" in _coverage(adapter, context)


def test_use_and_route_handlers_preserve_registration_order(tmp_path: Path) -> None:
    _write(
        tmp_path,
        """
const express = require('express'); const app = express();
function first(req,res,next) { next(); } function second(req,res,next) { next(); }
function routeFirst(req,res,next) { next(); } function routeSecond(req,res) { res.end(); }
function apiOnly(req,res,next) { next(); } function apiary(req,res) { res.end(); }
app.use(first); app.use('/ordered', second); app.use('/api', apiOnly); app.get('/ordered', routeFirst, routeSecond); app.get('/apiary', apiary);
""",
    )
    adapter, context, entries = _entries(tmp_path)
    route = next(item for item in entries if item.public_path == "/ordered")
    assert [
        role
        for role in _roles(adapter, context, route)
        if role in {"middleware", "route_handler"}
    ] == [
        "middleware",
        "middleware",
        "route_handler",
        "route_handler",
    ]
    pipeline = adapter.pipeline(context, route)
    targets = [
        segment.target.local_key
        for segment in pipeline
        if isinstance(segment.target, FrameworkLocalTarget)
    ]
    assert targets == [
        _function_key(tmp_path, "first"),
        _function_key(tmp_path, "first"),
        _function_key(tmp_path, "second"),
        _function_key(tmp_path, "second"),
        _function_key(tmp_path, "routeFirst"),
        _function_key(tmp_path, "routeFirst"),
        _function_key(tmp_path, "routeSecond"),
        _function_key(tmp_path, "routeSecond"),
    ]
    assert not {
        "error_middleware_arity_unresolved",
        "handler_target_unresolved",
    } & _coverage(adapter, context)
    apiary = next(item for item in entries if item.public_path == "/apiary")
    assert _roles(adapter, context, apiary).count("middleware") == 1


def test_computed_handler_collection_is_partial_with_explicit_order_uncertainty(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        """
const express = require('express'); const app = express();
app.use(...middleware); app.get('/items', ...handlers);
""",
    )
    adapter, context, entries = _entries(tmp_path)
    assert {"middleware_order_unresolved", "handler_target_unresolved"} <= _coverage(
        adapter, context
    )
    assert all(
        not any(
            segment.framework_role in {"middleware", "route_handler"}
            for segment in adapter.pipeline(context, entry)
        )
        for entry in entries
    )


def test_proven_next_forms_select_the_correct_continuation(tmp_path: Path) -> None:
    _write(
        tmp_path,
        """
const express = require('express'); const app = express();
function one(req,res,next) { next(); } function two(req,res,next) { next('route'); }
function skipped(req,res) { res.end(); } function later(req,res,next) { const err = new TypeError('x'); next(err); }
function normal(req,res) { res.end(); } function error(err,req,res,next) { res.send('error'); }
app.get('/next', one, two, skipped); app.all('/next', later); app.use(normal); app.use(error);
""",
    )
    adapter, context, entries = _entries(tmp_path)
    first = next(item for item in entries if item.public_path == "/next")
    roles = _roles(adapter, context, first)
    assert "continuation_next" in roles
    assert "continuation_next_route" in roles
    assert "error_middleware" in roles
    assert roles.index("error_middleware") > roles.index("continuation_next_route")
    assert "skipped" not in [
        segment.target.descriptor.public_name
        if hasattr(segment.target, "descriptor")
        else None
        for segment in adapter.pipeline(context, first)
    ]
    transition = next(
        segment
        for segment in adapter.pipeline(context, first)
        if segment.framework_role == "continuation_next_error"
    )
    assert transition.target.descriptor.public_name == "TypeError"


def test_computed_next_argument_is_partial_without_an_invented_continuation(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        """
const express = require('express'); const app = express();
function handler(req,res,next) { next(mode); } app.get('/next', handler);
""",
    )
    adapter, context, entries = _entries(tmp_path)
    assert {"continuation_kind_unresolved", "error_flow_unresolved"} <= _coverage(
        adapter, context
    )
    assert all(
        not any(
            segment.framework_role.startswith("continuation_")
            or segment.framework_role in {"error_transition", "error_middleware"}
            for segment in adapter.pipeline(context, entry)
        )
        for entry in entries
    )


def test_send_json_end_and_redirect_are_terminal_outcomes(tmp_path: Path) -> None:
    _write(
        tmp_path,
        """
const express = require('express'); const app = express();
function send(req,res) { res.send('x'); } function json(req,res) { res.json({}); }
function end(req,res) { res.end(); } function redirect(req,res) { res.redirect('/login'); } function unreachable(req,res) { res.end(); }
app.get('/send', send, unreachable); app.get('/json', json); app.get('/end', end); app.get('/redirect', redirect);
""",
    )
    adapter, context, entries = _entries(tmp_path)
    for path, terminal in (
        ("/send", "send"),
        ("/json", "json"),
        ("/end", "end"),
        ("/redirect", "redirect"),
    ):
        entry = next(item for item in entries if item.public_path == path)
        pipeline = adapter.pipeline(context, entry)
        terminal_segment = next(
            item for item in pipeline if item.framework_role == f"terminal_{terminal}"
        )
        assert terminal_segment.success_successor.kind == "return"
        assert "unreachable" not in [
            item.target.descriptor.public_name
            if hasattr(item.target, "descriptor")
            else None
            for item in pipeline
        ]


def test_computed_response_terminal_is_partial_without_an_invented_outcome(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        """
const express = require('express'); const app = express();
function handler(req,res) { res[terminal](value); } app.get('/x', handler);
""",
    )
    adapter, context, entries = _entries(tmp_path)
    assert "response_outcome_unresolved" in _coverage(adapter, context)
    assert all(
        not any(
            segment.framework_role.startswith("terminal_")
            for segment in adapter.pipeline(context, entry)
        )
        for entry in entries
    )


def test_direct_throw_and_returned_async_rejection_enter_error_flow(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        """
const express = require('express'); const app = express();
function thrown(req,res) { throw new TypeError('x'); }
function rejected(req,res) { return Promise.reject(new RangeError('y')); }
function ordinary(req,res) { res.end(); } function error(err,req,res,next) { res.send('error'); }
function pre(req,res,next) { next(); } function unreachable(req,res) { res.end(); }
app.use(pre); app.get('/throw', thrown, unreachable); app.get('/reject', rejected, unreachable); app.use(ordinary); app.use(error);
""",
    )
    adapter, context, entries = _entries(tmp_path)
    for path in ("/throw", "/reject"):
        route = next(item for item in entries if item.public_path == path)
        roles = _roles(adapter, context, route)
        assert "error_middleware" in roles
        assert "middleware" in roles
        assert "unreachable" not in [
            segment.target.descriptor.public_name
            if hasattr(segment.target, "descriptor")
            else None
            for segment in adapter.pipeline(context, route)
        ]
        transition = next(
            segment
            for segment in adapter.pipeline(context, route)
            if segment.framework_role == "error_transition"
        )
        assert transition.target.descriptor.public_name == (
            "TypeError" if path == "/throw" else "RangeError"
        )


def test_detached_async_rejection_is_partial_with_explicit_error_flow_uncertainty(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        """
const express = require('express'); const app = express();
function handler(req,res) { Promise.reject(new Error('x')); } app.get('/x', handler);
""",
    )
    adapter, context, entries = _entries(tmp_path)
    assert "async_error_flow_unresolved" in _coverage(adapter, context)
    assert all(
        not any(
            segment.framework_role in {"error_transition", "error_middleware"}
            for segment in adapter.pipeline(context, entry)
        )
        for entry in entries
    )


def test_four_parameter_error_middleware_is_selected_by_arity(tmp_path: Path) -> None:
    _write(
        tmp_path,
        """
const express = require('express'); const app = express(); const other = express.Router();
function boom(req,res) { throw new Error('x'); } function ordinary(req,res,next) { next(); }
function error(err,req,res,next) { res.send('error'); } function otherError(err,req,res,next) { res.end(); }
app.get('/x', boom); app.use(ordinary); other.use('/other', otherError); app.use('/x', error);
""",
    )
    adapter, context, entries = _entries(tmp_path)
    route = next(item for item in entries if item.public_path == "/x")
    selected = [
        segment.target.descriptor.public_name
        if hasattr(segment.target, "descriptor")
        else None
        for segment in adapter.pipeline(context, route)
        if segment.framework_role == "error_middleware"
    ]
    assert selected == ["error"]


def test_computed_error_middleware_target_is_partial_with_explicit_arity_uncertainty(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        """
const express = require('express'); const app = express();
function boom(req,res) { throw new Error('x'); }
app.get('/x', boom); app.use(errorHandler);
""",
    )
    adapter, context, entries = _entries(tmp_path)
    assert {
        "error_middleware_arity_unresolved",
        "handler_target_unresolved",
    } <= _coverage(adapter, context)
    route = next(item for item in entries if item.public_path == "/x")
    assert not any(
        segment.framework_role == "error_middleware"
        for segment in adapter.pipeline(context, route)
    )


def test_computed_registration_target_is_unresolved_without_an_invented_route(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        """
const express = require('express'); const app = express(); function handler() {}
// app.get('/phantom', handler);
function maybe(req,res,next) { res.send('next()'); }
app.get('/real', maybe); target.get('/x', handler);
""",
    )
    adapter, context, entries = _entries(tmp_path)
    assert [item.public_path for item in entries] == ["/real"]
    assert {
        "registration_target_unresolved",
        "route_method_unresolved",
        "route_path_unresolved",
        "handler_target_unresolved",
    } <= _coverage(adapter, context)
