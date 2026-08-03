from __future__ import annotations

from gateway.run import GatewayRunner


def test_gateway_has_no_automatic_backend_receiver_lifecycle() -> None:
    assert not hasattr(GatewayRunner, "_ensure_hades_persephone_supervisor")
    assert not hasattr(GatewayRunner, "_start_hades_persephone_receiver")
    assert not hasattr(GatewayRunner, "_stop_hades_persephone_receiver")
