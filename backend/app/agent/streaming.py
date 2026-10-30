"""Bounded SSE stream of real operations with disconnect cancellation."""

import asyncio
import json
from contextlib import suppress
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from starlette.responses import JSONResponse, StreamingResponse
from backend.app.agent.activity import activity_scope
from backend.app.core.principal import current_principal, principal_scope
from backend.app.models.provider_routing import provider_scope, selected_provider

_active_streams: dict[str, set[asyncio.Task]] = {}


async def cancel_workspace_streams(tenant_id: str) -> int:
    tasks = list(_active_streams.get(tenant_id, set()))
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    return len(tasks)


_SAFE_ACTIVITY_FIELDS = {
    "sequence", "kind", "stage", "status", "summary", "duration_ms", "wait_seconds",
    "result_count", "event_id", "query", "rationale", "source_strategy",
    "sufficient_evidence", "objective", "research_queries", "research_questions",
    "findings", "missing_information", "next_action", "question", "conclusion",
    "actions", "skipped_sources", "sources", "category", "request_id",
    "provider", "validation_error_code", "tool_name", "expected_argument_names",
    "actual_argument_names", "attempt_number",
}


def investigation_stream(request, settings, operation, on_complete=None):
    actor, provider = current_principal(settings), selected_provider(settings)

    async def generate():
        queue = asyncio.Queue(maxsize=128)
        sequence = 0
        visible_activity = []

        async def publish(event):
            nonlocal sequence
            sequence += 1
            safe = {key: value for key, value in event.items() if key in _SAFE_ACTIVITY_FIELDS}
            safe["summary"] = str(safe.get("summary", ""))[:600]
            safe["sequence"] = sequence
            visible_activity.append(safe)
            await queue.put({"event": "activity", "data": safe})

        async def run():
            try:
                with principal_scope(actor), provider_scope(provider, publish), activity_scope(publish):
                    result = await operation()
                if on_complete and not isinstance(result, JSONResponse):
                    await on_complete(result, visible_activity)
                if isinstance(result, JSONResponse):
                    await queue.put({"event": "error", "data": json.loads(result.body)})
                else:
                    await queue.put({"event": "result", "data": jsonable_encoder(result)})
            except HTTPException as error:
                await queue.put({"event": "error", "data": {"detail": error.detail, "status": error.status_code}})
            except asyncio.CancelledError:
                raise
            except Exception:
                await queue.put({"event": "error", "data": {"detail": "The investigation could not finish. Your saved work is preserved."}})
            finally:
                await queue.put(None)

        task = asyncio.create_task(run(), name="investigation-stream")
        _active_streams.setdefault(actor.tenant_id, set()).add(task)
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), 10)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if item is None:
                    break
                yield "event: " + item["event"] + "\ndata: " + json.dumps(item["data"], ensure_ascii=False) + "\n\n"
        finally:
            _active_streams.get(actor.tenant_id, set()).discard(task)
            task.cancel()
            # Cancellation must not wait on a producer blocked on a full queue.
            while not queue.empty():
                queue.get_nowait()
            with suppress(asyncio.CancelledError):
                await task

    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no"})
