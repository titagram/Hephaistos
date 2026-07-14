"""Shared prompt builder for the read-only ``/gnothi_seauton`` surface.

The command becomes a normal user turn on every conversational surface.  The
fixed prefix deliberately contains the complete operating contract so it is
byte-stable and does not require rebuilding the system prompt or tool schema.
"""

from __future__ import annotations


_GNOTHI_PREFIX = """\
[/gnothi_seauton] Perform a read-only, evidence-backed inspection of the
installed Hades organism. Use the existing local Hades CLI and graph tools;
when using graph tools, always set `scope=organism`.

Operating contract:
- Treat the current immutable organism revision as the source of claims.
- Distinguish observed evidence from inference. Report stale, partial, and
  unknown state explicitly; never fill gaps with plausible guesses.
- A bare request means: check status, then give a concise self-summary of the
  current organism, its capabilities, protected invariants, and evidence gaps.
- Preserve the exact local CLI semantics:
  `hades gnothi-seauton status --json`
  `hades gnothi-seauton inspect <component> --json`
  `hades gnothi-seauton explain <capability> --json`
  `hades gnothi-seauton diff <revision-a> <revision-b> --json`
  `hades gnothi-seauton wiki`
- If no revision exists, report that status and suggest a local rebuild; do
  not silently rebuild it during this read-only command.
- This command does not authorize mutation, external research, download,
  install, autopoiesis, or any other evolution action. Do not change files,
  configuration, dependencies, services, remote systems, or memories.
"""


def build_gnothi_prompt(user_request: str) -> str:
    """Return the stable inspection contract plus the user's requested view."""
    request = (user_request or "").strip()
    if not request:
        request = "status and concise self-summary"
    return f"{_GNOTHI_PREFIX}\nREQUESTED VIEW:\n{request}"


__all__ = ["build_gnothi_prompt"]
