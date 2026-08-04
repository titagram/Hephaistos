#!/usr/bin/env python3
"""Verify the Hermes supermemory memory-provider connection; optionally run an
E2E store -> ingest -> search -> forget -> cleanup cycle.

Usage:
    $HERMES_HOME/hermes-agent/venv/bin/python verify_supermemory.py [--e2e] [--hermes-home PATH]

Reads SUPERMEMORY_API_KEY from $HERMES_HOME/.env and config from
$HERMES_HOME/supermemory.json (base_url must point at the backend, e.g. a
self-hosted instance like https://persephone.cc).

Exit codes: 0 ok, 1 probe failed, 2 ingestion timed out (E2E only).
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path


def _load_key(hermes_home: str) -> str:
    env_file = Path(hermes_home) / ".env"
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.startswith("SUPERMEMORY_API_KEY="):
            return line.strip().split("=", 1)[1]
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--e2e", action="store_true",
                    help="run store -> search -> forget -> cleanup cycle (writes and deletes a test memory)")
    ap.add_argument("--hermes-home",
                    default=os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    args = ap.parse_args()

    hermes_home = args.hermes_home
    repo = Path(hermes_home) / "hermes-agent"
    sys.path.insert(0, str(repo))

    from plugins.memory.supermemory import (  # type: ignore
        _SupermemoryClient,
        _format_connection_summary,
        _load_supermemory_config,
        _probe_supermemory_connection,
    )

    key = _load_key(hermes_home)
    status = _probe_supermemory_connection(key, hermes_home)
    print(_format_connection_summary(status))
    if not status.get("ok"):
        return 1

    if not args.e2e:
        return 0

    cfg = _load_supermemory_config(hermes_home)
    client = _SupermemoryClient(api_key=key.strip(), timeout=cfg["api_timeout"],
                                container_tag=cfg["container_tag"],
                                search_mode=cfg["search_mode"], base_url=cfg["base_url"])

    content = "E2E verify: Hermes supermemory provider reachable"
    doc = client.add_memory(content, metadata={"sm_e2e": "1"})
    doc_id = doc.get("id", "")
    print("stored document:", doc_id)

    # Ingestion is async (~30-40 s): poll search until a memory entry with real
    # content appears (document/chunk hits carry an empty `memory` field).
    entry_id = None
    for _ in range(12):
        time.sleep(5)
        for hit in client.search_memories("hermes provider reachable", limit=5):
            if hit.get("id") != doc_id and hit.get("memory"):
                entry_id = hit["id"]
                break
        if entry_id:
            break
    if not entry_id:
        print("memory entry not ingested in time; test document left in place")
        return 2

    print("memory entry:", entry_id)
    client.forget_memory(entry_id)
    client._client.documents.delete(id=doc_id)  # SDK 3.56: kwarg is `id`, not `document_id`
    print("cleanup ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
