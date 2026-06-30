from pathlib import Path


def test_first_run_prompt_uses_hades_brand_and_command():
    source = Path("hermes_cli/main.py").read_text(encoding="utf-8")

    assert "It looks like Hades isn't configured yet -- no API keys or providers found." in source
    assert "  Run:  hades setup" in source
    assert "You can run 'hades setup' at any time to configure." in source
    assert "It looks like Hermes isn't configured yet -- no API keys or providers found." not in source
    assert "  Run:  hermes setup" not in source


def test_noninteractive_setup_guidance_uses_hades_brand_and_command():
    source = Path("hermes_cli/setup.py").read_text(encoding="utf-8")

    assert "♇ Hades Setup — Non-interactive mode" in source
    assert "Configure Hades using environment variables or config commands:" in source
    assert "  hades config set model.provider custom" in source
    assert "Run 'hades setup' in an interactive terminal to use the full wizard." in source
    assert "⚕ Hermes Setup — Non-interactive mode" not in source
    assert "Configure Hermes using environment variables or config commands:" not in source


def test_interactive_setup_wizard_uses_hades_brand_and_recommends_full_setup():
    source = Path("hermes_cli/setup.py").read_text(encoding="utf-8")

    assert "│             ♇ Hades Agent Setup Wizard                 │" in source
    assert "│  Let's configure your Hades Agent installation.        │" in source
    assert "How would you like to set up Hades?" in source
    assert "Full setup — configure every provider, tool & option yourself (recommended)" in source
    assert "Blank Slate — everything off except the bare minimum; opt in to each capability" in source
    assert "│             ⚕ Hermes Agent Setup Wizard                │" not in source
    assert "│  Let's configure your Hermes Agent installation.       │" not in source
    assert "How would you like to set up Hermes?" not in source
    assert "Quick Setup (Nous Portal) — free OAuth login, no API keys, model + tools (recommended)" not in source


def test_openclaw_migration_guidance_uses_hades_brand_and_command():
    source = Path("hermes_cli/setup.py").read_text(encoding="utf-8")

    assert "Hades can preview what would be imported before making any changes." in source
    assert "Skipping migration. You can run it later with: hades claw migrate --dry-run" in source
    assert "Migration cancelled. You can run it later with: hades claw migrate" in source
    assert "already exist in Hades (use hades claw migrate --overwrite to force)" in source
    assert "Hermes can preview what would be imported before making any changes." not in source
    assert "hermes claw migrate --dry-run" not in source


def test_setup_summary_uses_hades_commands_and_not_ready_without_provider():
    source = Path("hermes_cli/setup.py").read_text(encoding="utf-8")
    summary = source.split("def _print_setup_summary", 1)[1].split(
        "def _prompt_container_resources", 1
    )[0]

    assert "hades setup" in summary
    assert "hades setup model" in summary
    assert "hades config edit" in summary
    assert "hades gateway" in summary
    assert "hades doctor" in summary
    assert "Setup saved. Configure a model/provider before chatting." in summary
    assert "Configure model/provider" in summary
    assert "hermes setup" not in summary
    assert "hermes config" not in summary
    assert "hermes gateway" not in summary
    assert "hermes doctor" not in summary


def test_auxiliary_nous_guidance_uses_hades_auth_command():
    source = Path("agent/auxiliary_client.py").read_text(encoding="utf-8")

    assert "(run: hades auth)." in source
    assert "(run: hades auth add nous)." in source
    assert "run: hermes auth" not in source


def test_cli_slice_rebranding_guard_for_status_config_claw_inventory():
    status = Path("hermes_cli/status.py").read_text(encoding="utf-8")
    config = Path("hermes_cli/config.py").read_text(encoding="utf-8")
    claw = Path("hermes_cli/claw.py").read_text(encoding="utf-8")
    inventory = Path("hermes_cli/inventory.py").read_text(encoding="utf-8")

    assert "│                 ♇ Hades Agent Status" in status
    assert "not logged in (run: hades portal)" in status
    assert "not logged in (run: hades model)" in status
    assert "Run 'hades doctor' for detailed diagnostics" in status
    assert "│                 ⚕ Hermes Agent Status" not in status
    assert "not logged in (run: hermes portal)" not in status
    assert "not logged in (run: hermes model)" not in status
    assert "Run 'hermes doctor' for detailed diagnostics" not in status

    assert "│              ♇ Hades Configuration" in config
    assert "hades config edit      # Edit config file" in config
    assert "Usage: hades config set <key> <value>" in config
    assert "Run 'hades config migrate' to add them" in config
    assert "│              ⚕ Hermes Configuration" not in config
    assert "Usage: hermes config set <key> <value>" not in config
    assert "Run 'hermes config migrate' to add them" not in config

    assert "Usage: hades claw <command> [options]" in claw
    assert "│          ♇ Hades — OpenClaw Migration" in claw
    assert "│          ♇ Hades — OpenClaw Cleanup" in claw
    assert "Migrate settings from OpenClaw to Hades" in claw
    assert "Usage: hermes claw <command> [options]" not in claw
    assert "│          ⚕ Hermes — OpenClaw Migration" not in claw
    assert "│          ⚕ Hermes — OpenClaw Cleanup" not in claw
    assert "Migrate settings from OpenClaw to Hermes" not in claw

    assert 'f"run `hades model` to configure ({auth_type})"' in inventory
    assert 'f"run `hermes model` to configure ({auth_type})"' not in inventory
