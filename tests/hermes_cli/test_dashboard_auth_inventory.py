"""Static inventory checks for dashboard public API exposure."""

from __future__ import annotations

from pathlib import Path

from fastapi.routing import APIRoute

from hermes_cli import web_server
from hermes_cli.dashboard_auth.public_paths import PUBLIC_API_PATHS


READ_ONLY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
SELF_AUTHENTICATING_PUBLIC_API = frozenset({
    # NAS managed-cron callback. Public only to bypass cookie auth; the route
    # verifies its own purpose-scoped JWT before doing work.
    "/api/cron/fire",
})
MATRIX_DOC = Path(__file__).resolve().parents[2] / "docs/security/dashboard-auth-matrix.md"


def _api_routes() -> dict[str, frozenset[str]]:
    routes: dict[str, set[str]] = {}
    for route in web_server.app.routes:
        if not isinstance(route, APIRoute):
            continue
        path = str(route.path)
        if not path.startswith("/api/"):
            continue
        routes.setdefault(path, set()).update(route.methods or set())
    return {path: frozenset(methods) for path, methods in routes.items()}


def test_public_api_allowlist_routes_exist_and_are_exact() -> None:
    api_routes = _api_routes()

    assert all(path.startswith("/api/") for path in PUBLIC_API_PATHS)
    assert all("{" not in path and "}" not in path for path in PUBLIC_API_PATHS)
    assert PUBLIC_API_PATHS <= api_routes.keys()


def test_public_api_allowlist_is_read_only_or_self_authenticating() -> None:
    api_routes = _api_routes()
    unsafe: list[str] = []

    for path in sorted(PUBLIC_API_PATHS):
        methods = api_routes[path]
        mutating = methods - READ_ONLY_METHODS
        if not mutating:
            continue
        if path in SELF_AUTHENTICATING_PUBLIC_API:
            continue
        unsafe.append(f"{path} exposes {sorted(mutating)}")

    assert not unsafe, "public dashboard API routes must be read-only: " + "; ".join(unsafe)


def test_dashboard_auth_matrix_documents_public_api_allowlist() -> None:
    text = MATRIX_DOC.read_text(encoding="utf-8")

    for path in sorted(PUBLIC_API_PATHS):
        assert path in text
    for path in sorted(SELF_AUTHENTICATING_PUBLIC_API):
        assert path in text
    assert "Self-authenticated" in text
