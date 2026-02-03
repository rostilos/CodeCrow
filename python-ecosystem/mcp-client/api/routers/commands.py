"""
Command API endpoints (summarize, ask).
"""
import json
import asyncio
from typing import Dict, Any
from fastapi import APIRouter, Request
from starlette.responses import StreamingResponse

from model.dtos import (
    SummarizeRequestDto, SummarizeResponseDto,
    AskRequestDto, AskResponseDto,
)
from service.command.command_service import CommandService

router = APIRouter(tags=["commands"])

# Service instance
_command_service = None


def get_command_service() -> CommandService:
    """Get or create the command service singleton."""
    global _command_service
    if _command_service is None:
        _command_service = CommandService()
    return _command_service


@router.post("/review/summarize", response_model=SummarizeResponseDto)
async def summarize_endpoint(req: SummarizeRequestDto, request: Request):
    """
    HTTP endpoint to process /codecrow summarize command.
    
    Generates a comprehensive PR summary with:
    - Overview of changes
    - Key files modified
    - Impact analysis
    - Architecture diagram (Mermaid or ASCII)
    """
    command_service = get_command_service()
    
    try:
        wants_stream = _wants_streaming(request)

        if not wants_stream:
            # Non-streaming behavior
            result = await command_service.process_summarize(req)
            return SummarizeResponseDto(
                summary=result.get("summary"),
                diagram=result.get("diagram"),
                diagramType=result.get("diagramType", "MERMAID"),
                error=result.get("error")
            )

        # Streaming behavior
        async def event_stream():
            queue = asyncio.Queue()

            yield _json_event({"type": "status", "state": "queued", "message": "summarize request received"})

            def event_callback(event: Dict[str, Any]):
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    pass

            async def runner():
                try:
                    result = await command_service.process_summarize(req, event_callback=event_callback)
                    await queue.put({
                        "type": "final",
                        "result": result
                    })
                except Exception as e:
                    await queue.put({"type": "error", "message": str(e)})

            task = asyncio.create_task(runner())

            async for event in _drain_queue_until_final(queue, task):
                yield _json_event(event)

        return StreamingResponse(event_stream(), media_type="application/x-ndjson")

    except Exception as e:
        return SummarizeResponseDto(error=f"Summarize failed: {str(e)}")


@router.post("/review/ask", response_model=AskResponseDto)
async def ask_endpoint(req: AskRequestDto, request: Request):
    """
    HTTP endpoint to process /codecrow ask command.
    
    Answers questions about:
    - Specific issues
    - PR changes
    - Codebase (using RAG)
    - Analysis results
    """
    command_service = get_command_service()
    
    try:
        wants_stream = _wants_streaming(request)

        if not wants_stream:
            # Non-streaming behavior
            result = await command_service.process_ask(req)
            return AskResponseDto(
                answer=result.get("answer"),
                error=result.get("error")
            )

        # Streaming behavior
        async def event_stream():
            queue = asyncio.Queue()

            yield _json_event({"type": "status", "state": "queued", "message": "ask request received"})

            def event_callback(event: Dict[str, Any]):
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    pass

            async def runner():
                try:
                    result = await command_service.process_ask(req, event_callback=event_callback)
                    await queue.put({
                        "type": "final",
                        "result": result
                    })
                except Exception as e:
                    await queue.put({"type": "error", "message": str(e)})

            task = asyncio.create_task(runner())

            async for event in _drain_queue_until_final(queue, task):
                yield _json_event(event)

        return StreamingResponse(event_stream(), media_type="application/x-ndjson")

    except Exception as e:
        return AskResponseDto(error=f"Ask failed: {str(e)}")


def _wants_streaming(request: Request) -> bool:
    """Check if client wants streaming response."""
    accept_header = request.headers.get("accept", "")
    return "application/x-ndjson" in accept_header.lower()


def _json_event(event: Dict[str, Any]) -> str:
    """Serialize event to NDJSON line."""
    return json.dumps(event) + "\n"


async def _drain_queue_until_final(queue: asyncio.Queue, task: asyncio.Task):
    """
    Drain events from queue until we see a final/error event and task is done.
    Yields each event as it arrives.
    """
    seen_final = False

    while True:
        try:
            # Wait for event with timeout to prevent hanging
            event = await asyncio.wait_for(queue.get(), timeout=1.0)
            yield event

            # Check if this is a terminal event
            event_type = event.get("type")
            if event_type in ("final", "error"):
                seen_final = True
                # Continue draining in case there are more events

            # If we've seen final and task is done, check for remaining events then exit
            if seen_final and task.done():
                # Give a moment for any last events
                await asyncio.sleep(0.1)
                try:
                    while True:
                        event = queue.get_nowait()
                        yield event
                except asyncio.QueueEmpty:
                    break
                break

        except asyncio.TimeoutError:
            # No event available, check if task is done
            if task.done():
                # Task finished, drain any remaining events
                try:
                    while True:
                        event = queue.get_nowait()
                        yield event
                        if event.get("type") in ("final", "error"):
                            seen_final = True
                except asyncio.QueueEmpty:
                    pass

                # If we saw a final event or no more events, we're done
                if seen_final or task.done():
                    break
            # Otherwise continue waiting for events
