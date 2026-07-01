from __future__ import annotations

import json
from types import SimpleNamespace


def test_hades_coordination_profiles_are_curated_and_local_only():
    from hermes_cli.hades_coordination import hades_coordination_profiles

    profiles = hades_coordination_profiles()
    ids = {profile["id"] for profile in profiles}

    assert {"planner", "implementer", "reviewer", "sync-curator", "memory-steward"}.issubset(ids)
    for profile in profiles:
        routing = profile["model_routing"]
        assert profile["backend_visible"] is False
        assert routing["provider_source"] == "config.yaml"
        assert "local_model_profile" in routing
        assert "provider" not in routing
        assert "model" not in routing


def test_hades_coordination_profiles_are_copy_safe():
    from hermes_cli.hades_coordination import hades_coordination_profiles

    profiles = hades_coordination_profiles()
    profiles[0]["toolsets"].append("mutated")

    fresh = hades_coordination_profiles()

    assert "mutated" not in fresh[0]["toolsets"]


def test_hades_backend_profiles_json(capsys):
    import hermes_cli.hades_backend_cmd as cmd

    rc = cmd.hades_backend_command(SimpleNamespace(backend_action="profiles", json=True))

    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["local_only"] is True
    assert payload["backend_visible"] is False
    assert payload["config_source"] == "config.yaml"
    assert payload["skill"] == "autonomous-ai-agents/hades-coordination"
    assert payload["profiles"][0]["model_routing"]["provider_source"] == "config.yaml"
