<p align="center">
  <img src="assets/banner.png" alt="Hades Agent" width="100%">
</p>

# Hades Agent ☤
<p align="center">
  <a href="https://hades-agent.local/">Hades Agent</a> | <a href="https://hades-agent.local/">Hades Desktop</a>
</p>
<p align="center">
  <a href="https://hades-agent.local/docs/"><img src="https://img.shields.io/badge/Docs-hades--agent.hades-agent.local-FFD700?style=for-the-badge" alt="Documentation"></a>
  <a href="https://discord.gg/hades-agent"><img src="https://img.shields.io/badge/Discord-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Discord"></a>
  <a href="https://github.com/gabriele/hades-agent/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License: MIT"></a>
  <a href="https://hades-agent.local"><img src="https://img.shields.io/badge/Built%20for-Hades%20Agent-blueviolet?style=for-the-badge" alt="Built for Hades Agent"></a>
</p>

**The self-improving AI agent built by [Hades Agent](https://hades-agent.local).** It's the only agent with a built-in learning loop — it creates skills from experience, improves them during use, nudges itself to persist knowledge, searches its own past conversations, and builds a deepening model of who you are across sessions. Run it on a $5 VPS, a GPU cluster, or serverless infrastructure that costs nearly nothing when idle. It's not tied to your laptop — talk to it from Telegram while it works on a cloud VM.

Use any model you want — [Nous Portal](https://portal.hades-agent.local), OpenRouter, OpenAI, your own endpoint, and [many others](https://hades-agent.local/docs/integrations/providers). Switch with `hades model` — no code changes, no lock-in.

<table>
<tr><td><b>A real terminal interface</b></td><td>Full TUI with multiline editing, slash-command autocomplete, conversation history, interrupt-and-redirect, and streaming tool output.</td></tr>
<tr><td><b>Lives where you do</b></td><td>Telegram, Discord, Slack, WhatsApp, Signal, and CLI — all from a single gateway process. Voice memo transcription, cross-platform conversation continuity.</td></tr>
<tr><td><b>A closed learning loop</b></td><td>Agent-curated memory with periodic nudges. Autonomous skill creation after complex tasks. Skills self-improve during use. FTS5 session search with LLM summarization for cross-session recall. <a href="https://github.com/plastic-labs/honcho">Honcho</a> dialectic user modeling. Compatible with the <a href="https://agentskills.io">agentskills.io</a> open standard.</td></tr>
<tr><td><b>Scheduled automations</b></td><td>Built-in cron scheduler with delivery to any platform. Daily reports, nightly backups, weekly audits — all in natural language, running unattended.</td></tr>
<tr><td><b>Delegates and parallelizes</b></td><td>Spawn isolated subagents for parallel workstreams. Write Python scripts that call tools via RPC, collapsing multi-step pipelines into zero-context-cost turns.</td></tr>
<tr><td><b>Runs anywhere, not just your laptop</b></td><td>Six terminal backends — local, Docker, SSH, Singularity, Modal, and Daytona. Daytona and Modal offer serverless persistence — your agent's environment hibernates when idle and wakes on demand, costing nearly nothing between sessions. Run it on a $5 VPS or a GPU cluster.</td></tr>
<tr><td><b>Research-ready</b></td><td>Batch trajectory generation, trajectory compression for training the next generation of tool-calling models.</td></tr>
</table>

---

## Quick Install

### Linux, macOS, WSL2, Termux

```bash
curl -fsSL https://hades-agent.local/install.sh | bash
```

### Windows (native, PowerShell)

> **Heads up:** Native Windows runs Hades without WSL — CLI, gateway, TUI, and tools all work natively. If you'd rather use WSL2, the Linux/macOS one-liner above works there too. Found a bug? Please [file issues](https://github.com/gabriele/hades-agent/issues).

Run this in PowerShell:

```powershell
iex (irm https://hades-agent.local/install.ps1)
```

The installer handles everything: uv, Python 3.11, Node.js, ripgrep, ffmpeg, **and PortableGit** (unpacked to `%LOCALAPPDATA%\hermes\git` — no admin required, completely isolated from any system Git install). Hades uses this bundled Git Bash to run shell commands.

If you already have Git installed, the installer detects it and uses that instead. Otherwise PortableGit is downloaded into the Hades-managed compatibility storage root — it won't touch or interfere with any system Git.

> **Android / Termux:** The tested manual path is documented in the [Termux guide](https://hades-agent.local/docs/getting-started/termux). On Termux, Hades installs a curated `.[termux]` extra because the full `.[all]` extra currently pulls Android-incompatible voice dependencies.
>
> **Windows:** Native Windows is fully supported — the PowerShell one-liner above installs everything. If you'd rather use WSL2, the Linux command works there too. Native Windows install data remains under the compatibility path `%LOCALAPPDATA%\hermes`; WSL2 installs under `~/.hermes` as on Linux.

After installation:

```bash
source ~/.bashrc    # reload shell (or: source ~/.zshrc)
hades              # start chatting!
```

### Troubleshooting

#### Windows Defender or antivirus flags `uv.exe` as malware

If your antivirus (Bitdefender, Windows Defender, etc.) quarantines `uv.exe` from the Hades `bin` folder (`%LOCALAPPDATA%\hermes\bin\uv.exe`), this is a **false positive**. The file is Astral's `uv` — the Rust Python package manager Hades bundles to manage its Python environment. ML-based antivirus engines commonly flag unsigned Rust binaries that download and install packages.

**To verify your copy is authentic:**

```powershell
# Install GitHub CLI if needed
winget install --id GitHub.cli

# Login to GitHub
gh auth login

# Run verification
$uv = "$env:LOCALAPPDATA\hermes\bin\uv.exe"
$ver = (& $uv --version).Split(' ')[1]
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$zip = "$env:TEMP\uv.zip"
Invoke-WebRequest "https://github.com/astral-sh/uv/releases/download/$ver/uv-x86_64-pc-windows-msvc.zip" -OutFile $zip -UseBasicParsing
gh attestation verify $zip --repo astral-sh/uv
Expand-Archive $zip "$env:TEMP\uv_x" -Force
(Get-FileHash "$env:TEMP\uv_x\uv.exe").Hash -eq (Get-FileHash $uv).Hash
```

If attestation says "Verification succeeded" and the last line prints `True`, you're good.

**To whitelist Hades:**
- **Windows Defender:** Run PowerShell as Admin → `Add-MpPreference -ExclusionPath "$env:LOCALAPPDATA\hermes\bin"`
- **Bitdefender:** Add an exception in the Bitdefender console (Protection > Antivirus > Settings > Manage Exceptions)
- Whitelist the **folder**, not the file hash — Hades updates `uv` and the hash changes every version

For more context, see the upstream Astral reports: [astral-sh/uv#13553](https://github.com/astral-sh/uv/issues/13553), [astral-sh/uv#15011](https://github.com/astral-sh/uv/issues/15011), [astral-sh/uv#10079](https://github.com/astral-sh/uv/issues/10079).

---

## Getting Started

```bash
hades              # Interactive CLI — start a conversation
hades model        # Choose your LLM provider and model
hades tools        # Configure which tools are enabled
hades config set   # Set individual config values
hades gateway      # Start the messaging gateway (Telegram, Discord, etc.)
hades setup        # Run the full setup wizard (configures everything at once)
hades claw migrate # Migrate from OpenClaw (if coming from OpenClaw)
hades update       # Update to the latest version
hades doctor       # Diagnose any issues
```

Backend MVP support runbooks live in
[`docs/hades/launch.md`](docs/hades/launch.md) and
[`docs/hades/support-runbook.md`](docs/hades/support-runbook.md). The launch
guide covers install, backend bootstrap, privacy, verification, and safe
troubleshooting; the support runbook lists recovery actions and what not to
send in support logs.

📖 **[Full documentation →](https://hades-agent.local/docs/)**

---

## Skip the API-key collection — Nous Portal

Hades works with whatever provider you want — that's not changing. But if you'd rather not collect five separate API keys for the model, web search, image generation, TTS, and a cloud browser, **[Nous Portal](https://portal.hades-agent.local)** covers all of them under one subscription:

- **300+ models** — pick any of them with `/model <name>`
- **Tool Gateway** — web search (Firecrawl), image generation (FAL), text-to-speech (OpenAI), cloud browser (Browser Use), all routed through your sub. No extra accounts.

One command from a fresh install:

```bash
hades setup --portal
```

That logs you in via OAuth, sets Nous as your provider, and turns on the Tool Gateway. Check what's wired up any time with `hades portal info`. Full details on the [Tool Gateway docs page](https://hades-agent.local/docs/user-guide/features/tool-gateway).

You can still bring your own keys per-tool whenever you want — the gateway is per-backend, not all-or-nothing.

---

## CLI vs Messaging Quick Reference

Hades has two entry points: start the terminal UI with `hades`, or run the gateway and talk to it from Telegram, Discord, Slack, WhatsApp, Signal, or Email. Once you're in a conversation, many slash commands are shared across both interfaces.

| Action                         | CLI                                           | Messaging platforms                                                              |
| ------------------------------ | --------------------------------------------- | -------------------------------------------------------------------------------- |
| Start chatting                 | `hades`                                      | Run `hades gateway setup` + `hades gateway start`, then send the bot a message |
| Start fresh conversation       | `/new` or `/reset`                            | `/new` or `/reset`                                                               |
| Change model                   | `/model [provider:model]`                     | `/model [provider:model]`                                                        |
| Set a personality              | `/personality [name]`                         | `/personality [name]`                                                            |
| Retry or undo the last turn    | `/retry`, `/undo`                             | `/retry`, `/undo`                                                                |
| Compress context / check usage | `/compress`, `/usage`, `/insights [--days N]` | `/compress`, `/usage`, `/insights [days]`                                        |
| Browse skills                  | `/skills` or `/<skill-name>`                  | `/<skill-name>`                                                                  |
| Interrupt current work         | `Ctrl+C` or send a new message                | `/stop` or send a new message                                                    |
| Platform-specific status       | `/platforms`                                  | `/status`, `/sethome`                                                            |

For the full command lists, see the [CLI guide](https://hades-agent.local/docs/user-guide/cli) and the [Messaging Gateway guide](https://hades-agent.local/docs/user-guide/messaging).

---

## Evidence-backed engineering reviews

Run an autonomous local review from a repository with either command name:

```bash
hades review --effort medium
# equivalent:
hermes review HEAD~3..HEAD --effort high
```

The review accepts local changes (including untracked files), a Git range, a
diff file, or a GitHub pull-request URL. It uses real pytest or Vitest probes
when applicable, records its evidence under `~/.hermes/reviews/`, and leaves
the verdict local: it never pushes, merges, comments on, approves, or requests
changes on a remote PR. Reviewing untrusted PR code requires a configured
sandbox or explicit approval before build/tests run; static review remains
available when execution is denied.

Automated sandbox execution for reviews currently supports **Docker only**.
The image must contain Node.js 22 or newer and pre-provision project test
dependencies at `/opt/hermes-review-dependencies`; review containers run with
Docker networking disabled and receive no provider credentials. Modal,
Daytona, SSH, and Singularity terminal backends currently return an
`inconclusive` executable check rather than falling back to the host. The
authority requires Unix peer credentials: Windows users must run Hades under
WSL; native Windows review sessions are not currently supported.

The deterministic review engine incorporates a provenance-pinned source slice
from [Qwen Code](https://github.com/QwenLM/qwen-code), licensed under
[Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0).

---

## Documentation

All documentation lives at **[hades-agent.local/docs](https://hades-agent.local/docs/)**:

| Section                                                                                             | What's Covered                                             |
| --------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| [Quickstart](https://hades-agent.local/docs/getting-started/quickstart)                 | Install → setup → first conversation in 2 minutes          |
| [CLI Usage](https://hades-agent.local/docs/user-guide/cli)                              | Commands, keybindings, personalities, sessions             |
| [Configuration](https://hades-agent.local/docs/user-guide/configuration)                | Config file, providers, models, all options                |
| [Messaging Gateway](https://hades-agent.local/docs/user-guide/messaging)                | Telegram, Discord, Slack, WhatsApp, Signal, Home Assistant |
| [Security](https://hades-agent.local/docs/user-guide/security)                          | Command approval, DM pairing, container isolation          |
| [Tools & Toolsets](https://hades-agent.local/docs/user-guide/features/tools)            | 40+ tools, toolset system, terminal backends               |
| [Skills System](https://hades-agent.local/docs/user-guide/features/skills)              | Procedural memory, Skills Hub, creating skills             |
| [Memory](https://hades-agent.local/docs/user-guide/features/memory)                     | Persistent memory, user profiles, best practices           |
| [MCP Integration](https://hades-agent.local/docs/user-guide/features/mcp)               | Connect any MCP server for extended capabilities           |
| [Cron Scheduling](https://hades-agent.local/docs/user-guide/features/cron)              | Scheduled tasks with platform delivery                     |
| [Context Files](https://hades-agent.local/docs/user-guide/features/context-files)       | Project context that shapes every conversation             |
| [Architecture](https://hades-agent.local/docs/developer-guide/architecture)             | Project structure, agent loop, key classes                 |
| [Contributing](https://hades-agent.local/docs/developer-guide/contributing)             | Development setup, PR process, code style                  |
| [CLI Reference](https://hades-agent.local/docs/reference/cli-commands)                  | All commands and flags                                     |
| [Environment Variables](https://hades-agent.local/docs/reference/environment-variables) | Complete env var reference                                 |

---

## Migrating from OpenClaw

If you're coming from OpenClaw, Hades can automatically import your settings, memories, skills, and API keys.

**During first-time setup:** The setup wizard (`hades setup`) automatically detects `~/.openclaw` and offers to migrate before configuration begins.

**Anytime after install:**

```bash
hades claw migrate              # Interactive migration (full preset)
hades claw migrate --dry-run    # Preview what would be migrated
hades claw migrate --preset user-data   # Migrate without secrets
hades claw migrate --overwrite  # Overwrite existing conflicts
```

What gets imported:

- **SOUL.md** — persona file
- **Memories** — MEMORY.md and USER.md entries
- **Skills** — user-created skills → `~/.hermes/skills/openclaw-imports/`
- **Command allowlist** — approval patterns
- **Messaging settings** — platform configs, allowed users, working directory
- **API keys** — allowlisted secrets (Telegram, OpenRouter, OpenAI, Anthropic, ElevenLabs)
- **TTS assets** — workspace audio files
- **Workspace instructions** — AGENTS.md (with `--workspace-target`)

See `hades claw migrate --help` for all options, or use the `openclaw-migration` skill for an interactive agent-guided migration with dry-run previews.

---

## Contributing

We welcome contributions! See the [Contributing Guide](https://hades-agent.local/docs/developer-guide/contributing) for development setup, code style, and PR process.

Quick start for contributors — use the standard installer, then work from the
full git checkout it creates at `$HERMES_HOME/hades-agent` (usually
`~/.hermes/hades-agent`). This matches the layout used by `hades update`, the
managed venv, lazy dependencies, gateway, and docs tooling.

```bash
curl -fsSL https://hades-agent.local/install.sh | bash
cd "${HERMES_HOME:-$HOME/.hermes}/hades-agent"
uv pip install -e ".[all,dev]"
scripts/run_tests.sh
```

Manual clone fallback (for throwaway clones/CI where you intentionally do not
want the managed install layout):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv .venv --python 3.11
source .venv/bin/activate
uv pip install -e ".[all,dev]"
scripts/run_tests.sh
```

---

## Community

- 💬 [Discord](https://discord.gg/hades-agent)
- 📚 [Skills Hub](https://agentskills.io)
- 🐛 [Issues](https://github.com/gabriele/hades-agent/issues)
- 🔌 [computer-use-linux](https://github.com/avifenesh/computer-use-linux) — Linux desktop-control MCP server for Hades and other MCP hosts, with AT-SPI accessibility trees, Wayland/X11 input, screenshots, and compositor window targeting.
- 🔌 [HermesClaw](https://github.com/AaronWong1999/hermesclaw) — Community WeChat bridge: Run Hades Agent and OpenClaw on the same WeChat account.

---

## License

MIT — see [LICENSE](LICENSE).

Built by [Hades Agent](https://hades-agent.local).
