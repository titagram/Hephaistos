from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_COMPOSE = REPO_ROOT / "docker-compose.production.yml"
PRODUCTION_DOC = REPO_ROOT / "docs" / "hades" / "docker-production.md"


def _load_production_compose() -> dict:
    return yaml.safe_load(PRODUCTION_COMPOSE.read_text(encoding="utf-8"))


def test_production_compose_avoids_host_network_and_public_ports():
    compose = _load_production_compose()
    services = compose["services"]

    for service in services.values():
        assert service.get("network_mode") != "host"

    hades = services["hades"]
    assert hades["ports"] == ["127.0.0.1:9119:9119"]
    assert hades["command"] == ["gateway", "run"]
    assert hades["networks"] == ["hades_egress"]
    assert compose["networks"]["hades_egress"]["driver"] == "bridge"


def test_production_compose_requires_dashboard_auth():
    env = _load_production_compose()["services"]["hades"]["environment"]

    required_auth_vars = {
        "HERMES_DASHBOARD_BASIC_AUTH_USERNAME",
        "HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH",
        "HERMES_DASHBOARD_BASIC_AUTH_SECRET",
    }

    assert env["HERMES_DASHBOARD"] == "1"
    assert env["HERMES_DASHBOARD_HOST"] == "0.0.0.0"
    for name in required_auth_vars:
        assert env[name].startswith("${")
        assert ":?set " in env[name]

    assert "API_SERVER_HOST" not in env
    assert "API_SERVER_KEY" not in env


def test_production_docker_docs_cover_operational_runbook():
    text = PRODUCTION_DOC.read_text(encoding="utf-8").lower()

    required_topics = [
        "docker-compose.production.yml",
        "auth",
        "egress",
        "dashboard exposure",
        "backup",
        "restore",
        "update and rollback",
        "break glass",
        "network_mode: host",
        "api_server_key",
    ]

    for topic in required_topics:
        assert topic in text
