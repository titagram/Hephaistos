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
