"""Tests for Global Gnothi Seauton structural organism store."""

from pathlib import Path
from hermes_cli.evolution.gnothi_store import GlobalGnothiStore


def test_global_gnothi_store(tmp_path: Path):
    store = GlobalGnothiStore(tmp_path / "organism")
    assert store.get_capabilities() == []
    assert store.is_capability_verified("webcam.capture") is False

    digest = store.register_capability("webcam.capture", "Camera image capture capability", verified=True)
    assert len(digest) == 64
    assert store.is_capability_verified("webcam.capture") is True

    caps = store.get_capabilities()
    assert len(caps) == 1
    assert caps[0].capability_key == "webcam.capture"
    assert caps[0].verified is True
