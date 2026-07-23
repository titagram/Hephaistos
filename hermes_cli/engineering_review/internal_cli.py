"""Unstable internal proxy for the live engineering-review authority.

This executable intentionally owns no run, capability, evidence writer,
bundle path, or bridge. It can only forward validated protocol requests to the
authority already held by the current public Hermes session.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from .authority import ReviewAuthorityClient, ReviewAuthorityUnavailable
from .protocol import (
    ENGINE_COMMANDS,
    MAX_REQUEST_BYTES,
    EngineProtocolError,
    EngineRequest,
)


_DEFAULT_TIMEOUT_SECONDS = 600.0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hermes-review-engine",
        description=(
            "Internal, unstable proxy to a live `hermes review` session. "
            "Not a standalone review engine."
        ),
    )
    parser.add_argument("operation", choices=("start", *sorted(ENGINE_COMMANDS)))
    parser.add_argument(
        "request_json",
        nargs="?",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--session-id", help=argparse.SUPPRESS)
    parser.add_argument("--run", help=argparse.SUPPRESS)
    return parser


def _current_session(requested: str) -> str:
    current = os.environ.get("HERMES_SESSION_ID", "")
    if not current or not hmac.compare_digest(current, requested):
        raise ValueError("session ID does not match the current Hermes session")
    return current


def _read_request(path_text: str) -> EngineRequest:
    path = Path(path_text)
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_REQUEST_BYTES + 1)
    except OSError as exc:
        raise ValueError("request JSON could not be read") from exc
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError("request JSON exceeds 1 MiB")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("request file is not valid UTF-8 JSON") from exc
    return EngineRequest.from_wire(value)


def _emit(value: object) -> None:
    print(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        session_id = _current_session(
            args.session_id or os.environ.get("HERMES_SESSION_ID", "")
        )
        client = ReviewAuthorityClient(session_id)
        if args.operation == "start":
            if args.request_json is not None or args.run is not None:
                raise ValueError("start does not accept request or run arguments")
            _emit(client.start())
            return 0

        if args.operation == "cleanup" and args.run is not None:
            if args.request_json is not None:
                raise ValueError("cleanup --run does not accept a request JSON path")
            _emit(
                client.cleanup(
                    args.run,
                    timeout=_DEFAULT_TIMEOUT_SECONDS,
                ).to_wire()
            )
            return 0
        if args.run is not None:
            raise ValueError("--run is accepted only by cleanup")
        if args.request_json is None:
            raise ValueError(f"{args.operation} requires a request JSON path")
        request = _read_request(args.request_json)
        if request.command != args.operation:
            raise ValueError("request command does not match the proxy operation")
        response = client.invoke(request, timeout=_DEFAULT_TIMEOUT_SECONDS)
        _emit(response.to_wire())
        return 0
    except (ValueError, EngineProtocolError, ReviewAuthorityUnavailable) as exc:
        parser.error(str(exc))
    return 2  # pragma: no cover - argparse.error always exits


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
