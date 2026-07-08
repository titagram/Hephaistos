"""Local worker for DevBoard plugin agent work items."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from threading import Event, Thread
from typing import Any, Callable

from hermes_cli import hades_backend_db as db
from hermes_cli.hades_backend_client import redact_secret

logger = logging.getLogger("hermes_cli.hades_backend")


AgentRunner = Callable[[str, dict[str, Any]], str | dict[str, Any]]


@dataclass(frozen=True)
class PluginWorkerResult:
    summary: dict[str, Any]
    exit_code: int


def _error_summary(code: str, message: str, next_step: str, **extra: Any) -> dict[str, Any]:
    error: dict[str, Any] = {
        "code": code,
        "message": message,
        "next_step": next_step,
    }
    error.update({key: value for key, value in extra.items() if value not in (None, "")})
    return {"error": error}


def run_plugin_worker_once(
    *,
    client_factory: Callable[[], object] | None = None,
    agent_runner: AgentRunner | None = None,
    project_id: str | None = None,
    local_workspace_id: str | None = None,
    agent_key: str = "local_agent",
    limit: int = 1,
    heartbeat_interval_seconds: float = 30.0,
    quiet: bool = False,
) -> PluginWorkerResult:
    """Claim and process up to ``limit`` queued plugin work items."""

    from hermes_cli import hades_backend_runtime as runtime

    with db.connect_closing() as conn:
        agent = db.get_default_agent(conn)

    if agent is None:
        logger.info(
            "hades_backend.worker.skipped",
            extra={"hades_event": "worker.skipped", "hades_reason": "not_configured"},
        )
        return PluginWorkerResult(
            _error_summary(
                "not_configured",
                "Hades backend is not configured.",
                "Run `hades backend setup` or `hades backend bootstrap` first.",
            ),
            1,
        )

    selected_project_id = str(project_id or agent.project_id).strip()
    selected_local_workspace_id = str(local_workspace_id or runtime.plugin_local_workspace_id()).strip()
    if not selected_local_workspace_id:
        logger.warning(
            "hades_backend.worker.skipped",
            extra={
                "hades_event": "worker.skipped",
                "hades_reason": "missing_local_workspace_id",
                "hades_project_id": selected_project_id,
                "hades_agent_key": agent_key,
            },
        )
        if not quiet:
            print(
                "backend worker: missing plugin local workspace id; pass --local-workspace-id "
                "or set backend.plugin_local_workspace_id"
            )
        return PluginWorkerResult(
            _error_summary(
                "missing_local_workspace_id",
                "Plugin local workspace id is missing.",
                "Run `hades backend worker-setup` in the project checkout, or pass --local-workspace-id.",
                project_id=selected_project_id,
                agent_key=agent_key,
            ),
            1,
        )

    try:
        client = client_factory() if client_factory is not None else runtime.plugin_work_items_client_from_config()
    except Exception as exc:
        logger.warning(
            "hades_backend.worker.client_error",
            extra={
                "hades_event": "worker.client_error",
                "hades_project_id": selected_project_id,
                "hades_agent_key": agent_key,
                "hades_error": redact_secret(str(exc)),
            },
        )
        if not quiet:
            print(f"backend worker: failed to create plugin client: {redact_secret(str(exc))}")
        return PluginWorkerResult(
            _error_summary(
                "plugin_client_error",
                f"Failed to create plugin API client: {redact_secret(str(exc))}",
                "Run `hades backend worker-setup` and verify plugin token configuration.",
                project_id=selected_project_id,
                agent_key=agent_key,
            ),
            1,
        )

    runner = agent_runner or _default_agent_runner
    claimed = completed = failed = skipped = 0
    logger.info(
        "hades_backend.worker.start",
        extra={
            "hades_event": "worker.start",
            "hades_project_id": selected_project_id,
            "hades_agent_key": agent_key,
            "hades_limit": max(1, int(limit or 1)),
        },
    )

    try:
        response = client.list_agent_work_items(
            project_id=selected_project_id,
            agent_key=agent_key,
            status="queued",
            limit=max(1, int(limit or 1)),
        )
        items = _response_work_items(response)
    except Exception as exc:
        logger.warning(
            "hades_backend.worker.list_error",
            extra={
                "hades_event": "worker.list_error",
                "hades_project_id": selected_project_id,
                "hades_agent_key": agent_key,
                "hades_error": redact_secret(str(exc)),
            },
        )
        if not quiet:
            print(f"backend worker: failed to list plugin work items: {redact_secret(str(exc))}")
        _close_client(client)
        return PluginWorkerResult(
            _error_summary(
                "list_work_items_failed",
                f"Failed to list plugin work items: {redact_secret(str(exc))}",
                "Check backend connectivity, plugin token scope, and project/workspace binding.",
                project_id=selected_project_id,
                agent_key=agent_key,
            ),
            1,
        )

    for item in items[: max(1, int(limit or 1))]:
        work_item_id = _work_item_id(item)
        if not work_item_id:
            skipped += 1
            continue
        payload = _work_item_payload(item)
        kind = _work_item_kind(item, payload)
        _record_item(
            item,
            work_item_id=work_item_id,
            project_id=selected_project_id,
            local_workspace_id=selected_local_workspace_id,
            agent_key=agent_key,
            kind=kind,
            status="queued",
            payload=payload,
        )

        lease_token = ""
        try:
            claim = client.claim_agent_work_item(
                work_item_id,
                local_workspace_id=selected_local_workspace_id,
            )
            claim_item = claim.get("item") if isinstance(claim.get("item"), dict) else item
            lease_token = str(claim.get("lease_token") or "").strip()
            if not lease_token:
                raise RuntimeError("claim response did not include lease_token")
            claimed += 1
            with db.connect_closing() as conn:
                db.update_plugin_work_item_status(conn, work_item_id, "claimed", lease_token=lease_token)
            logger.info(
                "hades_backend.worker.claimed",
                extra={
                    "hades_event": "worker.claimed",
                    "hades_project_id": selected_project_id,
                    "hades_agent_key": agent_key,
                    "hades_work_item_id": work_item_id,
                    "hades_kind": kind,
                },
            )

            client.heartbeat_agent_work_item(work_item_id, lease_token=lease_token)
            prompt = _prompt_from_work_item_payload(_work_item_payload(claim_item) or payload)
            if not prompt:
                raise RuntimeError(f"unsupported or empty plugin work item payload kind: {kind or 'unknown'}")

            runner_response = _run_with_periodic_heartbeat(
                client,
                work_item_id=work_item_id,
                lease_token=lease_token,
                runner=runner,
                prompt=prompt,
                item=claim_item,
                heartbeat_interval_seconds=heartbeat_interval_seconds,
            )
            result, chat_message, memory_entry = _normalize_runner_response(runner_response)
            if not chat_message:
                raise RuntimeError("local agent produced an empty response")

            client.complete_agent_work_item(
                work_item_id,
                lease_token=lease_token,
                chat_message=chat_message,
                memory_entry=memory_entry,
            )
            with db.connect_closing() as conn:
                db.update_plugin_work_item_status(conn, work_item_id, "completed", result=result)
            logger.info(
                "hades_backend.worker.completed",
                extra={
                    "hades_event": "worker.completed",
                    "hades_project_id": selected_project_id,
                    "hades_agent_key": agent_key,
                    "hades_work_item_id": work_item_id,
                    "hades_kind": kind,
                },
            )
            completed += 1
        except Exception as exc:
            failed += 1
            message = redact_secret(str(exc))
            logger.warning(
                "hades_backend.worker.failed",
                extra={
                    "hades_event": "worker.failed",
                    "hades_project_id": selected_project_id,
                    "hades_agent_key": agent_key,
                    "hades_work_item_id": work_item_id,
                    "hades_kind": kind,
                    "hades_error": message,
                },
            )
            if lease_token:
                try:
                    client.fail_agent_work_item(work_item_id, lease_token=lease_token, message=message)
                except Exception:
                    pass
            with db.connect_closing() as conn:
                db.update_plugin_work_item_status(conn, work_item_id, "failed", result={"message": message})
            if not quiet:
                print(f"backend worker: failed work item {work_item_id}: {message}")

    _close_client(client)
    logger.info(
        "hades_backend.worker.complete",
        extra={
            "hades_event": "worker.complete",
            "hades_project_id": selected_project_id,
            "hades_agent_key": agent_key,
            "hades_summary": {
                "listed": len(items),
                "claimed": claimed,
                "completed": completed,
                "failed": failed,
                "skipped": skipped,
            },
        },
    )
    return PluginWorkerResult(
        {
            "listed": len(items),
            "claimed": claimed,
            "completed": completed,
            "failed": failed,
            "skipped": skipped,
        },
        1 if failed else 0,
    )


def _default_agent_runner(prompt: str, item: dict[str, Any]) -> str:
    from run_agent import AIAgent

    agent = AIAgent(platform="hades_backend_worker")
    return agent.chat(prompt)


def _run_with_periodic_heartbeat(
    client: object,
    *,
    work_item_id: str,
    lease_token: str,
    runner: AgentRunner,
    prompt: str,
    item: dict[str, Any],
    heartbeat_interval_seconds: float,
) -> str | dict[str, Any]:
    interval = max(0.01, float(heartbeat_interval_seconds or 30.0))
    stop = Event()

    def heartbeat_loop() -> None:
        while not stop.wait(interval):
            try:
                client.heartbeat_agent_work_item(work_item_id, lease_token=lease_token)
            except Exception:
                # Completion/fail is the source of truth; a transient heartbeat
                # error should not kill an in-flight local agent run.
                pass

    thread = Thread(target=heartbeat_loop, name="hades-plugin-work-item-heartbeat", daemon=True)
    thread.start()
    try:
        return runner(prompt, item)
    finally:
        stop.set()
        thread.join(timeout=2.0)


def _response_work_items(response: dict[str, Any]) -> list[dict[str, Any]]:
    value = response.get("items", response.get("data", response.get("work_items", [])))
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _work_item_id(item: dict[str, Any]) -> str:
    return str(item.get("id") or item.get("work_item_id") or "").strip()


def _work_item_payload(item: dict[str, Any]) -> dict[str, Any]:
    value = item.get("payload")
    return value if isinstance(value, dict) else {}


def _work_item_kind(item: dict[str, Any], payload: dict[str, Any]) -> str:
    return str(
        item.get("kind")
        or item.get("type")
        or payload.get("kind")
        or payload.get("schema")
        or payload.get("type")
        or "unknown"
    ).strip()


def _prompt_from_work_item_payload(payload: dict[str, Any]) -> str:
    for key in ("prompt", "message", "content", "text"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    user_message = payload.get("user_message")
    if isinstance(user_message, dict):
        for key in ("content", "message", "text"):
            value = user_message.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    elif isinstance(user_message, str) and user_message.strip():
        return user_message.strip()

    messages = payload.get("messages")
    if isinstance(messages, list):
        for message in reversed(messages):
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "").lower()
            content = message.get("content")
            if role == "user" and isinstance(content, str) and content.strip():
                return content.strip()

    return ""


def _normalize_runner_response(response: str | dict[str, Any]) -> tuple[dict[str, Any], str, dict[str, Any] | None]:
    if isinstance(response, dict):
        chat_message = str(
            response.get("chat_message")
            or response.get("final_response")
            or response.get("message")
            or response.get("summary")
            or ""
        ).strip()
        memory_entry = response.get("memory_entry") if isinstance(response.get("memory_entry"), dict) else None
        result = dict(response)
        if chat_message:
            result.setdefault("final_response", chat_message)
        return result, chat_message, memory_entry
    chat_message = str(response or "").strip()
    return {"final_response": chat_message}, chat_message, None


def _record_item(
    item: dict[str, Any],
    *,
    work_item_id: str,
    project_id: str,
    local_workspace_id: str,
    agent_key: str,
    kind: str,
    status: str,
    payload: dict[str, Any],
) -> None:
    with db.connect_closing() as conn:
        db.upsert_plugin_work_item(
            conn,
            work_item_id=work_item_id,
            project_id=str(item.get("project_id") or project_id),
            repository_id=str(item.get("repository_id") or "") or None,
            local_workspace_id=local_workspace_id,
            agent_key=str(item.get("agent_key") or agent_key),
            kind=kind,
            status=status,
            payload=payload,
        )


def _close_client(client: object) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        close()
