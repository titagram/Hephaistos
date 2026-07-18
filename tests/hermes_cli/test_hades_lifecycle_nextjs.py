"""Golden behaviour for bounded, source-only Next.js lifecycle extraction."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from hermes_cli.hades_graph_config import load_hades_graph_index_config
from hermes_cli.hades_graph_v2.model import (
    EntrypointKind,
    FrameworkKnowledge,
    FrameworkRecord,
    MethodSemantics,
    SourceIdentity,
)
from hermes_cli.hades_index.lifecycle.frameworks.nextjs import (
    NextJSLifecycleAdapter,
    _detected_version,
)
from hermes_cli.hades_index.lifecycle.model import (
    ConfigLocatorIR,
    CoverageOutcome,
    ExtractionContext,
    FrameworkLocalTarget,
    SourceLocationIR,
)
from hermes_cli.hades_index.tree_sitter_adapter import ParsedFile, SyntaxIR


LANGUAGES = ("javascript", "typescript")


def _write(root: Path, path: str, content: str) -> None:
    destination = root / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")


def _location(root: Path, path: str) -> SourceLocationIR:
    content = (root / path).read_bytes()
    return SourceLocationIR(
        path,
        1,
        max(1, content.count(b"\n") + 1),
        hashlib.sha256(content).hexdigest(),
    )


def _context(
    root: Path,
    language: str,
    *,
    version: str = "15.5.7",
    detected: bool = True,
    package_version: str | None = None,
) -> ExtractionContext:
    package_path = "package.json"
    _write(
        root,
        package_path,
        json.dumps({"dependencies": {"next": package_version or version}}),
    )
    metadata = ConfigLocatorIR(_location(root, package_path), "dependencies/next", 0)
    records = (
        (
            FrameworkRecord(
                language=language,
                name="nextjs",
                version=version,
                detector="package_json",
                configuration_paths=(package_path,),
                knowledge=FrameworkKnowledge.VERIFIED,
            ),
        )
        if detected
        else ()
    )
    return ExtractionContext(
        workspace_root=root,
        project_id="project",
        workspace_binding_id="binding",
        source_identity=SourceIdentity(None, "a" * 64, False, None),
        graph_config=load_hades_graph_index_config({}),
        detected_languages=(language,),
        detected_frameworks=records,
        composer_metadata=(),
        python_metadata=(),
        package_metadata=(metadata,),
        tsconfig_metadata=(),
        file_accessor=lambda path: (root / path).read_bytes(),
    )


def _syntax(path: str, language: str) -> SyntaxIR:
    return SyntaxIR(ParsedFile(path, language, (), (), ()), ())


def _extract(root: Path, language: str, *paths: str):
    context = _context(root, language)
    adapter = NextJSLifecycleAdapter(language)
    candidates = adapter.entrypoints(
        context, tuple(_syntax(path, language) for path in paths)
    )
    return adapter, context, candidates


def _assert_candidates(adapter, context, candidates) -> None:
    """Assert common public-IR invariants on every emitted HTTP boundary."""

    assert tuple(candidates) == tuple(
        sorted(
            candidates, key=lambda item: item.registration_locator.source_location.path
        )
    )
    for candidate in candidates:
        assert candidate.framework == "nextjs"
        assert candidate.kind is EntrypointKind.HTTP_ROUTE
        assert candidate.registration_locator.source_location.path
        assert "/../" not in f"/{candidate.registration_locator.source_location.path}"
        assert candidate.registration_locator.source_location.path.startswith((
            "app/",
            "pages/",
            "middleware",
            "src/",
            "next.config",
        ))
        if candidate.public_path is not None:
            assert candidate.public_path.startswith("/")
            assert ".." not in candidate.public_path
        assert candidate.handler_local_key or candidate.unresolved_fact_local_key
        assert candidate.framework_segment_keys == tuple(
            segment.local_key for segment in adapter.pipeline(context, candidate)
        )
    events = adapter.coverage_events(context)
    assert events == tuple(
        sorted(
            events,
            key=lambda event: (
                event.path or "",
                event.capability.value,
                event.reason_code or "",
            ),
        )
    )


def _by_path(candidates, path: str):
    return [
        item
        for item in candidates
        if item.registration_locator.source_location.path == path
    ]


def _reasons(adapter, context) -> set[str | None]:
    return {event.reason_code for event in adapter.coverage_events(context)}


@pytest.mark.parametrize("language", LANGUAGES)
def test_app_route_static_get_and_post_exports_are_method_entrypoints(
    tmp_path: Path, language: str
) -> None:
    _write(
        tmp_path,
        "app/api/items/route.ts",
        "export async function GET() { return Response.json([]) }\n"
        "export const POST = async () => Response.json({})\n",
    )
    adapter, context, candidates = _extract(
        tmp_path, language, "app/api/items/route.ts"
    )

    assert [(item.public_path, item.methods) for item in candidates] == [
        ("/api/items", ("GET",)),
        ("/api/items", ("POST",)),
    ]
    assert all(item.method_semantics is MethodSemantics.EXPLICIT for item in candidates)
    assert all(item.handler_local_key is not None for item in candidates)
    _assert_candidates(adapter, context, candidates)


def test_app_route_reexport_with_unresolved_target_is_partial(tmp_path: Path) -> None:
    _write(tmp_path, "app/api/items/route.ts", 'export { GET } from "./handler"\n')
    adapter, context, candidates = _extract(
        tmp_path, "typescript", "app/api/items/route.ts"
    )

    assert len(candidates) == 1
    assert candidates[0].methods == ("GET",)
    assert candidates[0].handler_local_key is None
    assert candidates[0].unresolved_fact_local_key is not None
    assert "route_handler_export_target_unresolved" in _reasons(adapter, context)
    _assert_candidates(adapter, context, candidates)


def test_pages_api_exhaustive_method_switch_and_unrestricted_fallback(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "pages/api/items.ts",
        """export default function handler(req, res) {
  switch (req.method) {
    case "GET": return res.status(200).end()
    case "POST": return res.status(201).end()
    default: return res.status(405).end()
  }
}
""",
    )
    _write(
        tmp_path,
        "pages/api/fallback.ts",
        """export default function handler(req, res) {
  switch (req.method) {
    case "GET": return res.status(200).end()
  }
}
""",
    )
    adapter, context, candidates = _extract(
        tmp_path,
        "typescript",
        "pages/api/items.ts",
        "pages/api/fallback.ts",
    )

    explicit = _by_path(candidates, "pages/api/items.ts")[0]
    fallback = _by_path(candidates, "pages/api/fallback.ts")[0]
    assert explicit.methods == ("GET", "POST")
    assert explicit.method_semantics is MethodSemantics.EXPLICIT
    assert fallback.methods == ()
    assert fallback.method_semantics is MethodSemantics.UNRESTRICTED
    assert "pages_api_method_dispatch_unresolved" not in _reasons(adapter, context)
    _assert_candidates(adapter, context, candidates)


def test_pages_api_computed_method_dispatch_is_partial(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "pages/api/items.ts",
        "export default function handler(req, res) { return handlers[req.method](req, res) }\n",
    )
    adapter, context, candidates = _extract(
        tmp_path, "typescript", "pages/api/items.ts"
    )

    assert len(candidates) == 1
    assert candidates[0].method_semantics is MethodSemantics.UNRESTRICTED
    assert "pages_api_method_dispatch_unresolved" in _reasons(adapter, context)
    _assert_candidates(adapter, context, candidates)


def test_route_groups_dynamic_and_catch_all_segments_normalize_public_paths(
    tmp_path: Path,
) -> None:
    paths = (
        "app/(group)/api/[id]/route.ts",
        "app/api/[...slug]/route.ts",
        "app/api/[[...optional]]/route.ts",
    )
    for path in paths:
        _write(tmp_path, path, "export function GET() { return new Response() }\n")
    adapter, context, candidates = _extract(tmp_path, "typescript", *paths)

    assert [(item.public_path, item.methods) for item in candidates] == [
        ("/api/:id", ("GET",)),
        ("/api/:slug*", ("GET",)),
        ("/api/:optional*?", ("GET",)),
    ]
    _assert_candidates(adapter, context, candidates)


def test_unsupported_filesystem_segment_syntax_is_unresolved(tmp_path: Path) -> None:
    path = "app/api/[..id]/route.ts"
    _write(tmp_path, path, "export function GET() { return new Response() }\n")
    adapter, context, candidates = _extract(tmp_path, "typescript", path)

    assert candidates == ()
    assert "route_pattern_unresolved" in _reasons(adapter, context)
    _assert_candidates(adapter, context, candidates)


def test_detected_nextjs_version_precedes_package_metadata(tmp_path: Path) -> None:
    context = _context(
        tmp_path,
        "typescript",
        version="15.5.7",
        package_version="14.2.0",
    )

    assert _detected_version(context, "typescript") == ("15.5.7", "framework_record")


def test_nonliteral_package_version_without_detection_is_unresolved(
    tmp_path: Path,
) -> None:
    context = _context(
        tmp_path,
        "typescript",
        detected=False,
        package_version="${NEXT_VERSION}",
    )
    adapter = NextJSLifecycleAdapter("typescript")
    candidates = adapter.entrypoints(context, ())

    assert _detected_version(context, "typescript") == (None, "unresolved")
    assert candidates == ()
    assert "framework_version_unresolved" in _reasons(adapter, context)


def test_static_middleware_matcher_is_exact(tmp_path: Path) -> None:
    path = "middleware.ts"
    _write(
        tmp_path,
        path,
        """export const config = { matcher: ["/one", { source: "/two" }, "/three"] }
export function middleware() { return NextResponse.next() }
""",
    )
    adapter, context, candidates = _extract(tmp_path, "typescript", path)

    pipeline = adapter.pipeline(context, candidates[0])
    assert [segment.target.descriptor.public_name for segment in pipeline[:3]] == [
        "/one",
        "/two",
        "/three",
    ]
    _assert_candidates(adapter, context, candidates)


def test_computed_middleware_matcher_is_partial(tmp_path: Path) -> None:
    path = "middleware.ts"
    _write(
        tmp_path,
        path,
        "const matcher = process.env.MATCHER\nexport const config = { matcher }\n"
        "export function middleware() { return NextResponse.next() }\n",
    )
    adapter, context, candidates = _extract(tmp_path, "typescript", path)

    assert "middleware_matcher_unresolved" in _reasons(adapter, context)
    assert not [
        segment
        for segment in adapter.pipeline(context, candidates[0])
        if segment.framework_role == "middleware_matcher"
    ]
    _assert_candidates(adapter, context, candidates)


def test_middleware_redirect_response_and_next_outcomes(tmp_path: Path) -> None:
    path = "middleware.ts"
    _write(
        tmp_path,
        path,
        """export function middleware(request) {
  if (request.nextUrl.pathname === "/redirect") return NextResponse.redirect("/login")
  if (request.nextUrl.pathname === "/response") return new NextResponse("blocked")
  return NextResponse.next()
}
""",
    )
    adapter, context, candidates = _extract(tmp_path, "typescript", path)

    pipeline = adapter.pipeline(context, candidates[0])
    assert [segment.framework_role for segment in pipeline] == [
        "middleware_redirect",
        "middleware_response",
        "middleware_next",
    ]
    assert pipeline[0].target.descriptor.public_name == "/login"
    _assert_candidates(adapter, context, candidates)


def test_unproven_middleware_outcome_is_partial(tmp_path: Path) -> None:
    path = "middleware.ts"
    _write(
        tmp_path,
        path,
        "export function middleware(request) { return decide(request) }\n",
    )
    adapter, context, candidates = _extract(tmp_path, "typescript", path)

    assert "middleware_outcome_unresolved" in _reasons(adapter, context)
    _assert_candidates(adapter, context, candidates)


def test_static_next_config_rewrites_are_exact(tmp_path: Path) -> None:
    path = "next.config.ts"
    _write(
        tmp_path,
        path,
        """export default {
  async rewrites() { return { beforeFiles: [{ source: "/one", destination: "/a" }], afterFiles: [{ source: "/two", destination: "/b" }] } }
}
""",
    )
    adapter, context, candidates = _extract(tmp_path, "typescript", path)

    assert [(item.public_path, item.public_name) for item in candidates] == [
        ("/one", "/a"),
        ("/two", "/b"),
    ]
    assert [item.registration_locator.ordinal for item in candidates] == [0, 1]
    assert [
        adapter.pipeline(context, item)[0].framework_role for item in candidates
    ] == [
        "rewrite_before_files",
        "rewrite_after_files",
    ]
    _assert_candidates(adapter, context, candidates)


def test_computed_rewrite_entry_is_partial(tmp_path: Path) -> None:
    path = "next.config.ts"
    _write(
        tmp_path,
        path,
        'export default { async rewrites() { return [{ source: prefix, destination: "/a" }] } }\n',
    )
    adapter, context, candidates = _extract(tmp_path, "typescript", path)

    assert candidates == ()
    assert "framework_config_unresolved" in _reasons(adapter, context)
    _assert_candidates(adapter, context, candidates)


def test_static_next_config_redirects_are_exact(tmp_path: Path) -> None:
    path = "next.config.ts"
    _write(
        tmp_path,
        path,
        """export default {
  async redirects() { return [
    { source: "/old", destination: "/new", permanent: false },
    { source: "/forever", destination: "/ever", permanent: true },
  ] }
}
""",
    )
    adapter, context, candidates = _extract(tmp_path, "typescript", path)

    assert [(item.public_path, item.public_name) for item in candidates] == [
        ("/old", "/new"),
        ("/forever", "/ever"),
    ]
    assert [item.registration_locator.ordinal for item in candidates] == [0, 1]
    assert [
        adapter.pipeline(context, item)[0].framework_role for item in candidates
    ] == [
        "redirect_307",
        "redirect_308",
    ]
    _assert_candidates(adapter, context, candidates)


def test_computed_redirect_entry_is_partial(tmp_path: Path) -> None:
    path = "next.config.ts"
    _write(
        tmp_path,
        path,
        'export default { async redirects() { return [{ source: "/old", destination, permanent }] } }\n',
    )
    adapter, context, candidates = _extract(tmp_path, "typescript", path)

    assert candidates == ()
    assert "framework_config_unresolved" in _reasons(adapter, context)
    _assert_candidates(adapter, context, candidates)


def test_computed_middleware_config_reports_explicit_uncertainty(
    tmp_path: Path,
) -> None:
    path = "src/middleware.ts"
    _write(
        tmp_path,
        path,
        "export const config = { matcher: buildMatcher() }\n"
        "export function middleware() { return NextResponse.next() }\n",
    )
    adapter, context, candidates = _extract(tmp_path, "typescript", path)

    assert len(candidates) == 1
    event = next(
        item
        for item in adapter.coverage_events(context)
        if item.reason_code == "middleware_matcher_unresolved"
    )
    assert event.outcome is CoverageOutcome.PARTIAL
    assert not [
        segment
        for segment in adapter.pipeline(context, candidates[0])
        if segment.framework_role == "middleware_matcher"
    ]
    _assert_candidates(adapter, context, candidates)


def test_computed_rewrite_or_redirect_config_is_unresolved(tmp_path: Path) -> None:
    path = "next.config.ts"
    _write(
        tmp_path,
        path,
        'export default { async rewrites() { return enabled ? [{ source: "/a", destination: "/b" }] : [] } }\n',
    )
    adapter, context, candidates = _extract(tmp_path, "typescript", path)

    assert candidates == ()
    event = next(
        item
        for item in adapter.coverage_events(context)
        if item.reason_code == "framework_config_unresolved"
    )
    assert event.outcome is CoverageOutcome.UNSUPPORTED
    _assert_candidates(adapter, context, candidates)


def test_render_graph_files_are_not_http_entrypoints(tmp_path: Path) -> None:
    paths = (
        "app/page.tsx",
        "app/layout.tsx",
        "app/components/button.tsx",
        "app/api/items/route.ts",
        "pages/api/legacy.ts",
        "middleware.ts",
    )
    for path in paths:
        source = "export default function Page() { return null }\n"
        if path.endswith("route.ts"):
            source = "export function GET() { return new Response() }\n"
        elif path.endswith("legacy.ts"):
            source = "export default function handler(req, res) { return res.end() }\n"
        elif path == "middleware.ts":
            source = "export function middleware() { return NextResponse.next() }\n"
        _write(tmp_path, path, source)
    adapter, context, candidates = _extract(tmp_path, "typescript", *paths)

    assert {item.registration_locator.source_location.path for item in candidates} == {
        "app/api/items/route.ts",
        "pages/api/legacy.ts",
        "middleware.ts",
    }
    _assert_candidates(adapter, context, candidates)


def test_ambiguous_nonstandard_render_file_role_is_partial(tmp_path: Path) -> None:
    path = "app/api/items/endpoint.ts"
    _write(tmp_path, path, "export function GET() { return new Response() }\n")
    adapter, context, candidates = _extract(tmp_path, "typescript", path)

    assert candidates == ()
    assert "http_entrypoint_file_role_unresolved" in _reasons(adapter, context)
    _assert_candidates(adapter, context, candidates)
