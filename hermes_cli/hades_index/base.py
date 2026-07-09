"""Protocol for pluggable per-language code graph indexers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class LanguageIndexer(Protocol):
    """Contract implemented by each language module's `build_graph` function.

    Each module (typescript.py, sql.py, python.py, php.py) exposes a
    module-level `build_graph` matching this call signature; dispatch in
    __init__.py selects which one to invoke based on detected file types.
    """

    def __call__(
        self,
        workspace_root: Path,
        candidates: list[Path],
        omitted: list[dict[str, Any]],
        *,
        truncated: bool,
        max_symbols: int,
        max_edges: int,
        max_file_bytes: int,
    ) -> dict[str, Any]: ...
