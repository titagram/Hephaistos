"""Out-of-band notice generation using existing AgentNotice rail."""

from __future__ import annotations

from typing import Sequence

from agent.credits_tracker import AgentNotice
from .suggestions import SuggestionRecord


def generate_notices(
    suggestions: Sequence[SuggestionRecord],
    notice_min_score: float = 0.65,
) -> list[AgentNotice]:
    notices: list[AgentNotice] = []
    for sug in suggestions:
        if sug.state in ("eligible", "surfaced") and sug.score >= notice_min_score:
            msg = f"Autopoiesis opportunity detected: {sug.suggestion_id} — {sug.summary_reason} (Score: {sug.score:.2f})"
            notices.append(
                AgentNotice(
                    text=msg,
                    level="info",
                    kind="sticky",
                    ttl_ms=None,
                    key=f"autopoiesis.{sug.suggestion_id}",
                    id=sug.suggestion_id,
                )
            )
    return notices
