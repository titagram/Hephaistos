from __future__ import annotations

import hashlib
import math

import pytest

from hermes_cli.evolution.contract import (
    EvolutionContractError,
    bounded_reason,
    canonical_json_bytes,
    content_digest,
    require_digest,
    require_relative_posix_path,
    sha256_digest,
)


def test_canonical_json_bytes_is_independent_of_mapping_insertion_order() -> None:
    first = {"z": 1, "nested": {"b": 2, "a": 1}}
    second = {"nested": {"a": 1, "b": 2}, "z": 1}

    assert canonical_json_bytes(first) == canonical_json_bytes(second)


def test_canonical_json_bytes_preserves_unicode_as_utf8() -> None:
    assert canonical_json_bytes({"name": "café"}) == '{"name":"café"}'.encode()


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_canonical_json_bytes_rejects_non_finite_floats(value: float) -> None:
    with pytest.raises(EvolutionContractError) as error:
        canonical_json_bytes({"value": value})

    assert error.value.code == "invalid_canonical_value"


def test_canonical_json_bytes_rejects_non_string_mapping_keys() -> None:
    with pytest.raises(EvolutionContractError) as error:
        canonical_json_bytes({1: "value"})

    assert error.value.code == "invalid_mapping_key"


@pytest.mark.parametrize(
    "value",
    [
        {"nested": {"value": "\ud800"}},
        {"nested": {"\udfff": "value"}},
    ],
)
def test_canonical_json_bytes_rejects_unpaired_surrogates(value: object) -> None:
    with pytest.raises(EvolutionContractError) as error:
        canonical_json_bytes(value)

    assert error.value.code == "invalid_canonical_value"
    assert str(error.value) == "invalid_canonical_value"


def test_canonical_json_bytes_rejects_self_referential_list() -> None:
    value: list[object] = []
    value.append(value)

    with pytest.raises(EvolutionContractError) as error:
        canonical_json_bytes(value)

    assert error.value.code == "invalid_canonical_value"


def test_canonical_json_bytes_rejects_self_referential_mapping() -> None:
    value: dict[str, object] = {}
    value["self"] = value

    with pytest.raises(EvolutionContractError) as error:
        canonical_json_bytes(value)

    assert error.value.code == "invalid_canonical_value"


@pytest.mark.parametrize(
    "value",
    [
        b"secret",
        bytearray(b"secret"),
        {"unexpected"},
        frozenset({"unexpected"}),
        object(),
    ],
)
def test_canonical_json_bytes_rejects_non_json_types(value: object) -> None:
    with pytest.raises(EvolutionContractError) as error:
        canonical_json_bytes(value)

    assert error.value.code == "invalid_canonical_value"


def test_sha256_digest_hashes_exact_bytes() -> None:
    assert sha256_digest(b"identity") == hashlib.sha256(b"identity").hexdigest()


def test_content_digest_uses_exact_domain_framing() -> None:
    value = {"component": "workshop"}
    expected = hashlib.sha256(
        b"hades-evolution-manifest-v1\0" + canonical_json_bytes(value)
    ).hexdigest()

    assert (
        content_digest(value, domain="hades-evolution-manifest-v1") == expected
    )


def test_content_digest_separates_domains() -> None:
    value = {"component": "workshop"}

    assert content_digest(value, domain="manifest") != content_digest(
        value, domain="pointer"
    )


def test_bounded_reason_normalizes_whitespace_and_caps_length() -> None:
    assert bounded_reason("  first\r\nsecond  ", limit=12) == "first second"
    assert bounded_reason("x" * 20, limit=8) == "x" * 8


@pytest.mark.parametrize("limit", [0, -1])
def test_bounded_reason_rejects_non_positive_limits(limit: int) -> None:
    with pytest.raises(EvolutionContractError) as error:
        bounded_reason("reason", limit=limit)

    assert error.value.code == "invalid_reason_limit"


def test_require_digest_accepts_lowercase_sha256_hex() -> None:
    digest = "0123456789abcdef" * 4

    assert require_digest(digest) == digest


@pytest.mark.parametrize(
    "value",
    [
        "0123456789ABCDEF" * 4,
        "0" * 63,
        "0" * 65,
        "g" * 64,
        b"0" * 64,
        None,
    ],
)
def test_require_digest_rejects_noncanonical_values(value: object) -> None:
    with pytest.raises(EvolutionContractError) as error:
        require_digest(value)

    assert error.value.code == "invalid_digest"


def test_require_relative_posix_path_accepts_canonical_relative_path() -> None:
    path = "components/workshop/plugin.py"

    assert require_relative_posix_path(path) == path


@pytest.mark.parametrize(
    "value",
    [
        "",
        ".",
        "/absolute/path",
        "C:/windows/path",
        r"C:\windows\path",
        "//server/share/path",
        r"\\server\share\path",
        "https://example.test/component",
        "../escape",
        "component/../escape",
        "component\\file.py",
        None,
    ],
)
def test_require_relative_posix_path_rejects_unsafe_paths(value: object) -> None:
    with pytest.raises(EvolutionContractError) as error:
        require_relative_posix_path(value)

    assert error.value.code == "invalid_relative_posix_path"
