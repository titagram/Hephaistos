"""Lightweight collector ordering shared by parser and builder code."""

from __future__ import annotations


COLLECTOR_ORDER = (
    "source",
    "capabilities",
    "runtime",
    "contracts",
    "dependencies",
    "experience",
)
