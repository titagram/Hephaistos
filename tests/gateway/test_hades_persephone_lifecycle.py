from __future__ import annotations

from unittest.mock import Mock

import pytest

from gateway.run import GatewayRunner


def _runner(fake_receiver: object) -> GatewayRunner:
    runner = object.__new__(GatewayRunner)
    runner._hades_persephone_receiver = None
    runner._hades_persephone_receiver_factory = lambda: fake_receiver
    return runner


@pytest.mark.asyncio
async def test_gateway_starts_and_stops_receiver_once() -> None:
    receiver = Mock(spec=["start", "stop"])
    runner = _runner(receiver)

    await runner._start_hades_persephone_receiver()
    await runner._start_hades_persephone_receiver()

    receiver.start.assert_called_once_with()
    assert runner._hades_persephone_receiver is receiver

    await runner._stop_hades_persephone_receiver()
    await runner._stop_hades_persephone_receiver()

    receiver.stop.assert_called_once_with(timeout=5.0)
    assert runner._hades_persephone_receiver is None


@pytest.mark.asyncio
async def test_gateway_receiver_failure_degrades_without_crashing() -> None:
    receiver = Mock(spec=["start", "stop"])
    receiver.start.side_effect = RuntimeError("secret-token-must-not-leak")
    runner = _runner(receiver)
    recorded: list[tuple[str, str | None]] = []
    runner._record_hades_persephone_lifecycle = (
        lambda state, *, error=None: recorded.append((state, error))
    )

    await runner._start_hades_persephone_receiver()

    assert runner._hades_persephone_receiver is None
    assert recorded == [("failed", "secret-token-must-not-leak")]


@pytest.mark.asyncio
async def test_gateway_capability_gate_does_not_start_receiver() -> None:
    runner = _runner(None)
    recorded: list[tuple[str, str | None]] = []
    runner._record_hades_persephone_lifecycle = (
        lambda state, *, error=None: recorded.append((state, error))
    )

    await runner._start_hades_persephone_receiver()

    assert runner._hades_persephone_receiver is None
    assert recorded == [("disabled_capability", None)]
