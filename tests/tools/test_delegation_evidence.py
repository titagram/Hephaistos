import json

import pytest

from tools.delegation_evidence import (
    MAX_CONCLUSION_CHARS,
    EvidencePacket,
    build_evidence_packet,
    canonical_json_hash,
    evidence_is_stale,
    validate_evidence_packet,
)


def _packet(**overrides):
    values = {
        "contract_hash": "c1",
        "base_commit": "a" * 40,
        "diff_hash": "d1",
        "covered_files": ("a.py",),
        "verification": (),
    }
    values.update(overrides)
    return build_evidence_packet(**values)


def test_changed_diff_invalidates_packet():
    packet = _packet()

    assert not evidence_is_stale(
        packet,
        contract_hash="c1",
        base_commit="a" * 40,
        diff_hash="d1",
        dependency_hashes=(),
    )
    assert evidence_is_stale(
        packet,
        contract_hash="c1",
        base_commit="a" * 40,
        diff_hash="d2",
        dependency_hashes=(),
    )


def test_contract_base_dependency_and_covered_file_changes_invalidate_packet():
    packet = _packet(dependency_hashes=("dep-1",), covered_files=("b.py", "a.py"))

    common = {
        "contract_hash": "c1",
        "base_commit": "a" * 40,
        "diff_hash": "d1",
        "dependency_hashes": ("dep-1",),
        "covered_files": ("a.py", "b.py"),
    }
    assert not evidence_is_stale(packet, **common)
    assert evidence_is_stale(packet, **{**common, "contract_hash": "c2"})
    assert evidence_is_stale(packet, **{**common, "base_commit": "b" * 40})
    assert evidence_is_stale(packet, **{**common, "dependency_hashes": ("dep-2",)})
    assert evidence_is_stale(packet, **{**common, "covered_files": ("a.py",)})


def test_changed_verification_input_invalidates_packet():
    packet = _packet(verification=({"command": "pytest -q", "status": "passed"},))
    common = {
        "contract_hash": "c1",
        "base_commit": "a" * 40,
        "diff_hash": "d1",
        "dependency_hashes": (),
    }

    assert not evidence_is_stale(
        packet,
        **common,
        verification=({"command": "pytest -q", "status": "passed"},),
    )
    assert evidence_is_stale(
        packet,
        **common,
        verification=({"command": "pytest tests/unit -q", "status": "passed"},),
    )


def test_packet_serialization_has_no_messages_or_reasoning():
    packet = _packet(
        verification=({"command": "pytest -q", "status": "passed"},),
        conclusion="focused tests passed",
    )

    payload = packet.to_dict()
    serialized = json.dumps(payload)

    assert "messages" not in payload
    assert "reasoning" not in payload
    assert "transcript" not in serialized.lower()
    assert validate_evidence_packet(payload)


@pytest.mark.parametrize("forbidden", ["messages", "reasoning", "transcript"])
def test_packet_rejects_trajectory_fields_nested_in_verification(forbidden):
    with pytest.raises(ValueError, match="trajectory"):
        _packet(verification=({"status": "passed", forbidden: ["private"]},))


def test_conclusion_is_bounded_and_secret_bearing_evidence_is_rejected():
    packet = _packet(conclusion="x" * (MAX_CONCLUSION_CHARS + 20))
    assert len(packet.conclusion) == MAX_CONCLUSION_CHARS

    secret = "ghp_" + "A" * 40
    with pytest.raises(ValueError, match="secret"):
        _packet(conclusion=f"used {secret}")


def test_canonical_hash_is_order_independent_for_mapping_keys():
    assert canonical_json_hash({"b": 2, "a": 1}) == canonical_json_hash(
        {"a": 1, "b": 2}
    )


def test_validation_rejects_wrong_schema_and_non_packet_fields():
    payload = _packet().to_dict()
    payload["schema"] = "unknown"
    with pytest.raises(ValueError, match="schema"):
        validate_evidence_packet(payload)

    payload = _packet().to_dict()
    payload["messages"] = []
    with pytest.raises(ValueError, match="field"):
        validate_evidence_packet(payload)


def test_to_dict_returns_detached_verification_records():
    packet = _packet(verification=({"status": "passed", "details": {"count": 2}},))
    payload = packet.to_dict()
    payload["verification"][0]["details"]["count"] = 99

    assert packet.verification[0]["details"]["count"] == 2
    assert isinstance(packet, EvidencePacket)
