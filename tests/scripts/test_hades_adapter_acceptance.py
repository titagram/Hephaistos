from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "hades_adapter_acceptance.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("hades_adapter_acceptance", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _corpus() -> dict[str, object]:
    return {
        "schema": "hades.adapter_acceptance_corpus.v1",
        "framework": "fastapi",
        "framework_versions": ["fastapi==0.115.0", "starlette==0.37.2"],
        "sources": [
            {
                "kind": "official_docs",
                "name": "fastapi-official",
                "url": "https://github.com/fastapi/fastapi.git",
                "revision": "4" * 40,
                "paths": ["docs/en/docs"],
            },
            *[
                {
                    "kind": "public_repository",
                    "name": f"public-{index}",
                    "url": f"https://example.test/public-{index}.git",
                    "revision": str(index) * 40,
                    "paths": ["."],
                }
                for index in range(1, 4)
            ],
        ],
    }


def _matrix() -> dict[str, object]:
    return {
        "schema": "hades.adapter_acceptance_matrix.v1",
        "framework": "fastapi",
        "framework_versions": ["fastapi==0.115.0", "starlette==0.37.2"],
        "items": [
            {
                "id": "FASTAPI-ROUTE-001",
                "construct": "decorator_route",
                "common_rule": "official_core_idiom",
                "expected_precision": "exact",
                "sources": ["fastapi-official", "public-1"],
                "required_facts": ["method", "normalized_path", "endpoint"],
                "allowed_unknowns": [],
                "negative_variants": [
                    {
                        "id": "FASTAPI-ROUTE-001-N1",
                        "construct": "dynamic_decorator_target",
                        "expected_precision": "partial",
                        "required_uncertainty": ["dynamic_registration"],
                        "test_nodes": ["tests/example.py::test_dynamic_route"],
                    }
                ],
                "test_nodes": ["tests/example.py::test_decorator_route"],
            }
        ],
    }


def test_build_lock_is_permutation_stable_and_validates() -> None:
    module = _load_module()
    corpus = _corpus()
    matrix = _matrix()
    lock = module.build_lock(corpus, matrix)
    module.validate_lock(corpus, matrix, lock)
    assert lock["schema"] == "hades.adapter_acceptance_lock.v1"
    assert len(lock["bundle_sha256"]) == 64


def test_corpus_requires_exactly_three_public_repositories() -> None:
    module = _load_module()
    corpus = _corpus()
    corpus["sources"] = corpus["sources"][:-1]
    with pytest.raises(module.AcceptanceError, match="exactly 3 public_repository"):
        module.validate_corpus(corpus)


def test_documents_reject_unknown_keys() -> None:
    module = _load_module()
    corpus = _corpus()
    corpus["surprise"] = True
    with pytest.raises(module.AcceptanceError, match="unknown keys"):
        module.validate_corpus(corpus)


def test_source_revision_must_be_immutable_sha() -> None:
    module = _load_module()
    corpus = _corpus()
    corpus["sources"][0]["revision"] = "main"
    with pytest.raises(module.AcceptanceError, match="40 lowercase hex"):
        module.validate_corpus(corpus)


def test_matrix_ids_are_unique_across_items_and_negatives() -> None:
    module = _load_module()
    corpus = _corpus()
    matrix = _matrix()
    matrix["items"][0]["negative_variants"][0]["id"] = "FASTAPI-ROUTE-001"
    with pytest.raises(module.AcceptanceError, match="duplicate matrix id"):
        module.validate_matrix(matrix, corpus)


def test_exact_item_requires_a_finite_negative_envelope() -> None:
    module = _load_module()
    corpus = _corpus()
    matrix = _matrix()
    matrix["items"][0]["negative_variants"] = []
    with pytest.raises(module.AcceptanceError, match="negative variant"):
        module.validate_matrix(matrix, corpus)


def test_matrix_sources_must_exist_in_corpus() -> None:
    module = _load_module()
    corpus = _corpus()
    matrix = _matrix()
    matrix["items"][0]["sources"] = ["missing"]
    with pytest.raises(module.AcceptanceError, match="unknown corpus source"):
        module.validate_matrix(matrix, corpus)


def test_lock_mismatch_is_rejected() -> None:
    module = _load_module()
    corpus = _corpus()
    matrix = _matrix()
    lock = module.build_lock(corpus, matrix)
    lock["matrix_sha256"] = "0" * 64
    with pytest.raises(module.AcceptanceError, match="lock mismatch"):
        module.validate_lock(corpus, matrix, lock)


def test_nested_documents_require_closed_shapes() -> None:
    module = _load_module()
    corpus = _corpus()
    corpus["sources"][0]["extra"] = "not permitted"
    with pytest.raises(module.AcceptanceError, match="unknown keys"):
        module.validate_corpus(corpus)

    matrix = _matrix()
    del matrix["items"][0]["construct"]
    with pytest.raises(module.AcceptanceError, match="missing keys"):
        module.validate_matrix(matrix, _corpus())


def test_cli_freezes_validates_and_returns_two_for_invalid_lock(tmp_path: Path) -> None:
    module = _load_module()
    corpus_path = tmp_path / "corpus.json"
    matrix_path = tmp_path / "matrix.json"
    lock_path = tmp_path / "lock.json"
    corpus_path.write_text(json.dumps(_corpus()), encoding="utf-8")
    matrix_path.write_text(json.dumps(_matrix()), encoding="utf-8")

    assert (
        module.main([
            "freeze",
            "--corpus",
            str(corpus_path),
            "--matrix",
            str(matrix_path),
            "--lock",
            str(lock_path),
        ])
        == 0
    )
    assert (
        module.main([
            "validate",
            "--corpus",
            str(corpus_path),
            "--matrix",
            str(matrix_path),
            "--lock",
            str(lock_path),
        ])
        == 0
    )

    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["bundle_sha256"] = "0" * 64
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    assert (
        module.main([
            "validate",
            "--corpus",
            str(corpus_path),
            "--matrix",
            str(matrix_path),
            "--lock",
            str(lock_path),
        ])
        == 2
    )


@pytest.mark.parametrize("framework", ["fastapi", "express", "nextjs"])
def test_adapter_acceptance_bundle_is_frozen(framework: str) -> None:
    module = _load_module()
    base = ROOT / "tests" / "fixtures" / "hades" / "adapter_acceptance" / framework
    corpus = json.loads((base / "corpus.json").read_text(encoding="utf-8"))
    matrix = json.loads((base / "matrix.json").read_text(encoding="utf-8"))
    lock = json.loads((base / "lock.json").read_text(encoding="utf-8"))
    module.validate_lock(corpus, matrix, lock)


def test_fastapi_acceptance_matrix_has_exact_frozen_ids() -> None:
    base = ROOT / "tests" / "fixtures" / "hades" / "adapter_acceptance" / "fastapi"
    matrix = json.loads((base / "matrix.json").read_text(encoding="utf-8"))
    assert {item["id"] for item in matrix["items"]} == {
        "FASTAPI-ROUTE-001",
        "FASTAPI-ROUTER-001",
        "FASTAPI-METHOD-001",
        "FASTAPI-STARLETTE-001",
        "FASTAPI-DEPENDENCY-001",
        "FASTAPI-SECURITY-001",
        "FASTAPI-MIDDLEWARE-001",
        "FASTAPI-EXCEPTION-001",
        "FASTAPI-LIFESPAN-001",
        "FASTAPI-BACKGROUND-001",
        "FASTAPI-RESPONSE-001",
        "FASTAPI-IMPORT-001",
        "FASTAPI-DYNAMIC-001",
        "FASTAPI-REBIND-001",
    }
