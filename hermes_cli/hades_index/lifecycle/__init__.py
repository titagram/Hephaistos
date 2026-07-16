"""Language-neutral lifecycle extraction contracts.

Adapters import their immutable records from :mod:`.model`; later lifecycle
modules own CFG construction, framework semantics, and graph building.
"""

from .model import *  # noqa: F403
