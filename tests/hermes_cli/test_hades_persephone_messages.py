from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import time

import pytest

from hermes_cli.hades_persephone_messages import (
    EffectClass,
    MessageType,
    make_response,
    parse_envelope,
)


VALID = {
    "schema": "hades.persephone.agent-message.v1",
    "message_id": "msg_1",
    "correlation_id": "corr_1",
    "causation_id": None,
    "project_id": "proj_1",
    "sender_agent_id": "agent_a",
    "target_agent_id": "agent_b",
    "target_workspace_binding_id": "wb_b",
    "message_type": "information_request",
    "effect": "information_read",
    "capability": "source_search",
    "remote_task_id": "task_1",
    "remote_task_version": "7",
    "expires_at": 2_000_000_000,
    "payload": {"query": "Where is AuthService defined?"},
}


def test_workspace_request_requires_target_binding():
    raw = {**VALID, "target_workspace_binding_id": None}
    with pytest.raises(ValueError, match="target_workspace_binding_id"):
        parse_envelope(raw)


def test_target_binding_key_is_required_even_when_null_is_allowed():
    raw = {**VALID, "capability": "project_memory_search"}
    del raw["target_workspace_binding_id"]
    with pytest.raises(ValueError, match="missing envelope fields"):
        parse_envelope(raw)


def test_non_workspace_capability_allows_explicit_null_target_binding():
    raw = {
        **VALID,
        "capability": "project_memory_search",
        "target_workspace_binding_id": None,
    }
    assert parse_envelope(raw).target_workspace_binding_id is None


def test_cross_project_context_is_rejected():
    envelope = parse_envelope(VALID)
    with pytest.raises(ValueError, match="project"):
        envelope.validate_receiver(project_id="proj_2", agent_id="agent_b")


def test_wrong_agent_or_workspace_context_is_rejected():
    envelope = parse_envelope(VALID)
    with pytest.raises(ValueError, match="agent"):
        envelope.validate_receiver(project_id="proj_1", agent_id="agent_c")
    with pytest.raises(ValueError, match="workspace"):
        envelope.validate_receiver(
            project_id="proj_1",
            agent_id="agent_b",
            workspace_binding_id="wb_other",
        )


@pytest.mark.parametrize(
    "field",
    [
        "schema",
        "message_id",
        "correlation_id",
        "project_id",
        "sender_agent_id",
        "target_agent_id",
        "capability",
    ],
)
def test_blank_required_identifiers_are_rejected(field):
    with pytest.raises(ValueError, match=field):
        parse_envelope({**VALID, field: "  "})


def test_unknown_fields_are_rejected_instead_of_becoming_authority():
    raw = {
        **VALID,
        "approved": True,
        "sender_role": "admin",
        "token=very-secret-token": True,
    }
    with pytest.raises(ValueError, match="unknown envelope fields") as exc_info:
        parse_envelope(raw)
    assert "admin" not in str(exc_info.value)
    assert "very-secret-token" not in str(exc_info.value)


@pytest.mark.parametrize(
    "hostile_field",
    [
        "password=correct-horse-battery-staple",
        "authorization=Bearer secret-credential",
        "client_secret=private-client-value",
    ],
)
def test_unknown_field_error_never_echoes_untrusted_field_names(hostile_field):
    with pytest.raises(ValueError, match=r"unknown envelope fields \(1\)") as exc_info:
        parse_envelope({**VALID, hostile_field: True})
    message = str(exc_info.value)
    assert hostile_field not in message
    assert hostile_field.split("=", 1)[0] not in message


def test_invalid_enum_and_payload_shapes_are_rejected_without_echoing_secrets():
    with pytest.raises(ValueError, match="message_type"):
        parse_envelope({**VALID, "message_type": "execute_as_admin"})
    with pytest.raises(ValueError, match="payload"):
        parse_envelope({**VALID, "payload": ["token=very-secret-token"]})


def test_expired_message_is_rejected():
    with pytest.raises(ValueError, match="expired"):
        parse_envelope({**VALID, "expires_at": int(time.time()) - 1})


def test_boolean_expiry_is_not_treated_as_an_integer():
    with pytest.raises(ValueError, match="expires_at"):
        parse_envelope({**VALID, "expires_at": True})


def test_payload_is_bounded_by_canonical_utf8_json_bytes():
    oversized = {"query": "é" * 40_000}
    assert len(json.dumps(oversized, ensure_ascii=False).encode("utf-8")) > 65_536
    with pytest.raises(ValueError, match="65536"):
        parse_envelope({**VALID, "payload": oversized})


def test_envelope_and_nested_payload_are_immutable_copies():
    raw = {**VALID, "payload": {"query": "auth", "filters": ["src"]}}
    envelope = parse_envelope(raw)
    raw["payload"]["query"] = "changed"
    raw["payload"]["filters"].append("tests")

    assert envelope.payload["query"] == "auth"
    assert envelope.payload["filters"] == ("src",)
    with pytest.raises(TypeError):
        envelope.payload["query"] = "changed"
    with pytest.raises(FrozenInstanceError):
        envelope.project_id = "proj_2"


def test_envelope_has_a_json_serializable_round_trip_representation():
    envelope = parse_envelope(VALID)
    wire = envelope.to_dict()
    assert json.loads(json.dumps(wire))["payload"] == VALID["payload"]
    assert parse_envelope(wire) == envelope


def test_make_response_preserves_scope_and_correlation_but_reverses_agents():
    request = parse_envelope(VALID)
    response = make_response(
        request,
        message_id="msg_2",
        target_workspace_binding_id="wb_a",
        payload={"answer": "AuthService.php", "decision_status": "answered"},
        expires_at=2_000_000_100,
    )

    assert response.project_id == request.project_id
    assert response.sender_agent_id == request.target_agent_id
    assert response.target_agent_id == request.sender_agent_id
    assert response.target_workspace_binding_id == "wb_a"
    assert response.message_type is MessageType.INFORMATION_RESPONSE
    assert response.effect is EffectClass.INFORMATION_READ
    assert response.correlation_id == request.correlation_id
    assert response.causation_id == request.message_id
    assert response.remote_task_id == request.remote_task_id
    assert response.remote_task_version == request.remote_task_version


def test_make_response_refuses_non_request_messages():
    response_raw = {**VALID, "message_type": "information_response"}
    with pytest.raises(ValueError, match="request message"):
        make_response(
            parse_envelope(response_raw),
            message_id="msg_2",
            target_workspace_binding_id="wb_a",
            payload={"answer": "done"},
            expires_at=2_000_000_100,
        )


def test_openapi_freezes_queue_gate_envelope_and_targeted_cursors():
    root = Path(__file__).resolve().parents[2]
    spec = json.loads((root / "docs/hades/openapi-hades-v1.json").read_text())
    schema = spec["components"]["schemas"]["PersephoneMessageRequest"]
    required = set(schema["required"])
    assert required == {
        "schema",
        "message_id",
        "correlation_id",
        "project_id",
        "sender_agent_id",
        "target_agent_id",
        "target_workspace_binding_id",
        "message_type",
        "effect",
        "capability",
        "expires_at",
        "payload",
    }
    assert schema["additionalProperties"] is False
    assert schema["properties"]["payload"]["maxProperties"] > 0

    identifier_fields = {
        "message_id",
        "correlation_id",
        "causation_id",
        "project_id",
        "sender_agent_id",
        "target_agent_id",
        "target_workspace_binding_id",
        "capability",
        "remote_task_id",
        "remote_task_version",
    }
    for field in identifier_fields:
        prop = schema["properties"][field]
        assert prop["minLength"] == 1, field
        assert prop["pattern"] == r".*\S.*", field

    for route in ("inbox", "events"):
        params = {
            item["name"]: item
            for item in spec["paths"][f"/api/hades/v1/persephone/{route}"]["get"]["parameters"]
        }
        assert params["target_agent_id"]["required"] is True
        assert params["target_workspace_binding_id"]["required"] is False
        assert params["cursor"]["required"] is False
        for field in (
            "project_id",
            "target_agent_id",
            "target_workspace_binding_id",
            "cursor",
        ):
            assert params[field]["schema"]["minLength"] == 1, (route, field)
            assert params[field]["schema"]["pattern"] == r".*\S.*", (route, field)

    capabilities = spec["paths"]["/api/hades/v1/capabilities"]["get"]["responses"]["200"]
    assert "persephone_agent_queue_v1" in json.dumps(capabilities)


def test_operations_marks_agent_queue_as_a_capability_gated_handoff_contract():
    root = Path(__file__).resolve().parents[2]
    operations = (root / "docs/hades/operations.md").read_text()
    assert "persephone_agent_queue_v1" in operations
    assert "64 KiB" in operations
    assert "handoff contract" in operations
    assert "must not claim" in operations
