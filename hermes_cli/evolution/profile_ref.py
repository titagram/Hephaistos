"""Stable, privacy-safe profile reference for autopoiesis observation.

Produces a non-path, non-reversible opaque token (``prof_<sha256-prefix>``)
from the current ``HERMES_HOME`` path.  Stable within a profile: the same
path always yields the same token.  No raw path, username, or project name
leaks into the observation envelope.
"""

from __future__ import annotations

import hashlib

import hermes_constants


def get_profile_ref() -> str:
    """Return a stable, privacy-safe opaque profile reference.

    Uses SHA-256 of the resolved HERMES_HOME path, truncated to 12 hex
    chars and prefixed with ``prof_``.  This is safe for use in
    ``ObservationEnvelope.source_profile_ref`` — it is not a filesystem
    path and contains no sensitive material.
    """
    raw = str(hermes_constants.get_hermes_home().resolve())
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"prof_{h}"
