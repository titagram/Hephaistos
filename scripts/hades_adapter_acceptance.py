#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hermes_cli.hades_graph_v2.identity import sha256_jcs


_SHA_RE = re.compile(r"[0-9a-f]{40}")
_PRECISIONS = {"exact", "partial", "unresolved"}
_COMMON_RULES = {
    "official_core_idiom",
    "two_independent_repositories",
    "fundamental_lifecycle_stage",
    "owned_ordinary_idiom",
}


class AcceptanceError(ValueError):
    pass


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AcceptanceError(f"{label} must be an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise AcceptanceError(f"{label} must be an array")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise AcceptanceError(f"{label} must be a non-empty string")
    return value


def _string_list(value: Any, label: str) -> list[str]:
    values = _list(value, label)
    return [_string(item, f"{label}[{index}]") for index, item in enumerate(values)]


def _closed(document: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(document) - allowed)
    missing = sorted(allowed - set(document))
    if unknown:
        raise AcceptanceError(f"{label} has unknown keys: {unknown}")
    if missing:
        raise AcceptanceError(f"{label} is missing keys: {missing}")


def _test_nodes(value: Any, label: str) -> list[str]:
    nodes = _string_list(value, label)
    if not nodes or any("::test_" not in node for node in nodes):
        raise AcceptanceError(f"{label} must name pytest tests")
    return nodes


def validate_corpus(document: Any) -> None:
    corpus = _object(document, "corpus")
    _closed(corpus, {"schema", "framework", "framework_versions", "sources"}, "corpus")
    if corpus["schema"] != "hades.adapter_acceptance_corpus.v1":
        raise AcceptanceError("unsupported corpus schema")
    _string(corpus["framework"], "corpus.framework")
    versions = _string_list(corpus["framework_versions"], "corpus.framework_versions")
    if not versions or any("==" not in version for version in versions):
        raise AcceptanceError(
            "framework_versions must contain pinned name==version strings"
        )

    sources = _list(corpus["sources"], "corpus.sources")
    names: set[str] = set()
    counts = {"official_docs": 0, "public_repository": 0, "owned_repository": 0}
    for index, raw in enumerate(sources):
        source = _object(raw, f"source[{index}]")
        _closed(
            source, {"kind", "name", "url", "revision", "paths"}, f"source[{index}]"
        )
        kind = _string(source["kind"], f"source[{index}].kind")
        if kind not in counts:
            raise AcceptanceError(f"source[{index}].kind is unsupported")
        counts[kind] += 1
        name = _string(source["name"], f"source[{index}].name")
        if name in names:
            raise AcceptanceError(f"duplicate source name: {name}")
        names.add(name)
        _string(source["url"], f"source[{index}].url")
        revision = _string(source["revision"], f"source[{index}].revision")
        if _SHA_RE.fullmatch(revision) is None:
            raise AcceptanceError(
                f"source[{index}].revision must be 40 lowercase hex characters"
            )
        paths = _string_list(source["paths"], f"source[{index}].paths")
        if not paths:
            raise AcceptanceError(
                f"source[{index}].paths must contain non-empty strings"
            )

    if counts["official_docs"] < 1:
        raise AcceptanceError("corpus requires at least 1 official_docs source")
    if counts["public_repository"] != 3:
        raise AcceptanceError("corpus requires exactly 3 public_repository sources")
    if counts["owned_repository"] > 1:
        raise AcceptanceError("corpus permits at most 1 owned_repository source")


def validate_matrix(document: Any, corpus_document: Any) -> None:
    validate_corpus(corpus_document)
    corpus = _object(corpus_document, "corpus")
    matrix = _object(document, "matrix")
    _closed(matrix, {"schema", "framework", "framework_versions", "items"}, "matrix")
    if matrix["schema"] != "hades.adapter_acceptance_matrix.v1":
        raise AcceptanceError("unsupported matrix schema")
    if _string(matrix["framework"], "matrix.framework") != corpus["framework"]:
        raise AcceptanceError("matrix framework does not match corpus")
    if (
        _string_list(matrix["framework_versions"], "matrix.framework_versions")
        != corpus["framework_versions"]
    ):
        raise AcceptanceError("matrix framework_versions do not match corpus")

    source_names = {source["name"] for source in corpus["sources"]}
    seen_ids: set[str] = set()
    items = _list(matrix["items"], "matrix.items")
    if not items:
        raise AcceptanceError("matrix.items cannot be empty")
    for index, raw in enumerate(items):
        item = _object(raw, f"item[{index}]")
        _closed(
            item,
            {
                "id",
                "construct",
                "common_rule",
                "expected_precision",
                "sources",
                "required_facts",
                "allowed_unknowns",
                "negative_variants",
                "test_nodes",
            },
            f"item[{index}]",
        )
        item_id = _string(item["id"], f"item[{index}].id")
        if item_id in seen_ids:
            raise AcceptanceError(f"duplicate matrix id: {item_id}")
        seen_ids.add(item_id)
        _string(item["construct"], f"item[{index}].construct")
        common_rule = _string(item["common_rule"], f"item[{index}].common_rule")
        if common_rule not in _COMMON_RULES:
            raise AcceptanceError(f"item[{index}].common_rule is unsupported")
        precision = _string(
            item["expected_precision"], f"item[{index}].expected_precision"
        )
        if precision not in _PRECISIONS:
            raise AcceptanceError(f"item[{index}].expected_precision is unsupported")
        sources = _string_list(item["sources"], f"item[{index}].sources")
        if not sources or any(source not in source_names for source in sources):
            raise AcceptanceError(f"item[{index}] references an unknown corpus source")
        facts = _string_list(item["required_facts"], f"item[{index}].required_facts")
        _string_list(item["allowed_unknowns"], f"item[{index}].allowed_unknowns")
        _test_nodes(item["test_nodes"], f"item[{index}].test_nodes")
        negatives = _list(item["negative_variants"], f"item[{index}].negative_variants")
        if precision == "exact" and (not facts or not negatives):
            raise AcceptanceError(
                f"exact item {item_id} requires facts and a negative variant"
            )
        for negative_index, raw_negative in enumerate(negatives):
            negative = _object(
                raw_negative, f"item[{index}].negative[{negative_index}]"
            )
            _closed(
                negative,
                {
                    "id",
                    "construct",
                    "expected_precision",
                    "required_uncertainty",
                    "test_nodes",
                },
                f"item[{index}].negative[{negative_index}]",
            )
            negative_id = _string(negative["id"], "negative.id")
            if negative_id in seen_ids:
                raise AcceptanceError(f"duplicate matrix id: {negative_id}")
            seen_ids.add(negative_id)
            _string(negative["construct"], "negative.construct")
            negative_precision = _string(
                negative["expected_precision"], "negative.expected_precision"
            )
            if negative_precision not in {"partial", "unresolved"}:
                raise AcceptanceError(
                    f"negative {negative_id} must be partial or unresolved"
                )
            uncertainty = _string_list(
                negative["required_uncertainty"], "negative.required_uncertainty"
            )
            if not uncertainty:
                raise AcceptanceError(f"negative {negative_id} requires uncertainty")
            _test_nodes(negative["test_nodes"], f"negative {negative_id}.test_nodes")


def build_lock(corpus: Any, matrix: Any) -> dict[str, str]:
    validate_matrix(matrix, corpus)
    return {
        "schema": "hades.adapter_acceptance_lock.v1",
        "framework": corpus["framework"],
        "corpus_sha256": sha256_jcs(corpus),
        "matrix_sha256": sha256_jcs(matrix),
        "bundle_sha256": sha256_jcs({"corpus": corpus, "matrix": matrix}),
    }


def validate_lock(corpus: Any, matrix: Any, lock: Any) -> None:
    expected = build_lock(corpus, matrix)
    if lock != expected:
        raise AcceptanceError("acceptance lock mismatch")


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("freeze", "validate"):
        child = subparsers.add_parser(command)
        child.add_argument("--corpus", type=Path, required=True)
        child.add_argument("--matrix", type=Path, required=True)
        child.add_argument("--lock", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        corpus = _read(args.corpus)
        matrix = _read(args.matrix)
        lock = build_lock(corpus, matrix)
        if args.command == "freeze":
            args.lock.write_text(
                json.dumps(lock, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        else:
            validate_lock(corpus, matrix, _read(args.lock))
        print(lock["bundle_sha256"])
        return 0
    except (AcceptanceError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"acceptance error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
