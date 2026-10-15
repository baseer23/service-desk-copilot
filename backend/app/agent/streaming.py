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


def investigation_stream(request, settings, operation):
    actor, provider = current_principal(settings), selected_provider(settings)

    async def generate():
        queue = asyncio.Queue(maxsize=128)
        sequence = 0

        async def publish(event):
            nonlocal sequence
            sequence += 1
            await queue.put({"event": "activity", "data": {**event, "sequence": sequence}})

        async def run():
            try:
                with principal_scope(actor), provider_scope(provider, publish), activity_scope(publish):
                    result = await operation()
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
            task.cancel()
            # Cancellation must not wait on a producer blocked on a full queue.
            while not queue.empty():
                queue.get_nowait()
            with suppress(asyncio.CancelledError):
                await task

    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no"})
