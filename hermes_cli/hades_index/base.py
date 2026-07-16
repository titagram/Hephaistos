"""Closed v2 adapter boundary for language-neutral lifecycle extraction."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from hermes_cli.hades_index.lifecycle.model import AdapterResult, ExtractionContext


class LanguageIndexer(Protocol):
    """One language adapter; graph construction is intentionally out of scope."""

    def index(
        self,
        context: ExtractionContext,
        files: Sequence[Path],
    ) -> AdapterResult: ...
