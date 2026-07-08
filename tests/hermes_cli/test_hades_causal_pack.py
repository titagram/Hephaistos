from hermes_cli.hades_causal_pack import build_causal_pack, causal_pack_key, validate_causal_pack


def test_build_causal_pack_requires_replayable_refs():
    pack = build_causal_pack(
        {
            "project_id": "project_1",
            "binding_id": "binding_1",
            "bug_id": "bug_booking_overlap",
            "freshness": {"status": "current", "head_commit": "abc123"},
            "awareness": {"diagnosable_without_source": True},
            "evidence_refs": ["bug_evidence:booking_log"],
            "graph_refs": ["route:bookings.store", "symbol:BookingController@store"],
            "source_slice_refs": ["source_slice:booking_controller_store"],
            "diagnosis": {
                "root_cause_id": "booking-overlap-validation-gap",
                "bug_class": "validation",
                "failure_classification": "confirmed",
                "affected_refs": ["symbol:BookingController@store", "table:bookings"],
            },
        }
    )

    assert pack["schema"] == "hades.causal_pack.v1"
    assert pack["status"] == "valid"
    assert pack["root_cause_id"] == "booking-overlap-validation-gap"
    assert pack["replay"]["required_refs"] == [
        "bug_evidence:booking_log",
        "route:bookings.store",
        "symbol:BookingController@store",
        "source_slice:booking_controller_store",
    ]
    assert causal_pack_key(pack) == causal_pack_key(dict(reversed(list(pack.items()))))


def test_validate_causal_pack_blocks_precise_claim_without_slice_or_current_freshness():
    invalid = build_causal_pack(
        {
            "project_id": "project_1",
            "binding_id": "binding_1",
            "bug_id": "bug_booking_overlap",
            "freshness": {"status": "stale", "head_commit": "abc123"},
            "awareness": {"diagnosable_without_source": True},
            "evidence_refs": ["bug_evidence:booking_log"],
            "graph_refs": ["symbol:BookingController@store"],
            "source_slice_refs": [],
            "diagnosis": {
                "root_cause_id": "booking-overlap-validation-gap",
                "bug_class": "validation",
                "failure_classification": "confirmed",
                "affected_refs": ["symbol:BookingController@store"],
            },
        }
    )

    result = validate_causal_pack(invalid)

    assert result["status"] == "invalid"
    assert result["blockers"] == ["freshness_not_current", "source_slice_refs_required"]
