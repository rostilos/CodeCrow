"""
Review API endpoints.
"""
import json
import asyncio
from typing import Dict, Any
from fastapi import APIRouter, Request
from starlette.responses import StreamingResponse

from model.dtos import ReviewRequestDto, ReviewResponseDto
from service.review.review_service import ReviewService
from utils.response_parser import ResponseParser

router = APIRouter(tags=["review"])


def get_review_service(request: Request) -> ReviewService:
    """Retrieve the ReviewService instance created during app lifespan."""
    return request.app.state.review_service


@router.post("/review", response_model=ReviewResponseDto)
async def review_endpoint(req: ReviewRequestDto, request: Request):
    """
    HTTP endpoint to accept review requests from the pipeline agent.

    Behavior:
    - If the client requests streaming via header `Accept: application/x-ndjson`,
      the endpoint will return a StreamingResponse that yields NDJSON events as they occur.
    - Otherwise it preserves the original behavior and returns a single ReviewResponseDto JSON body.
    """
    review_service = get_review_service(request)
    
    try:
        wants_stream = _wants_streaming(request)

        if not wants_stream:
            # Non-streaming (legacy) behavior
            result = await review_service.process_review_request(req)
            return ReviewResponseDto(
                result=result.get("result"),
                error=result.get("error")
            )

        # Streaming behavior
        async def event_stream():
            queue = asyncio.Queue()

            # Emit initial queued status
            yield _json_event({"type": "status", "state": "queued", "message": "request received"})

            # Event callback to capture service events
            def event_callback(event: Dict[str, Any]):
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    pass  # Skip if queue is full

            # Run processing in background
            async def runner():
                try:
                    result = await review_service.process_review_request(
                        req,
                        event_callback=event_callback
                    )
                    # Emit final event with result
                    final_event = {
                        "type": "final",
                        "result": result.get("result")
                    }
                    await queue.put(final_event)
                except Exception as e:
                    await queue.put({
                        "type": "error",
                        "message": str(e)
                    })

            task = asyncio.create_task(runner())

            # Drain queue and yield events
            async for event in _drain_queue_until_final(queue, task):
                yield _json_event(event)

        return StreamingResponse(event_stream(), media_type="application/x-ndjson")

    except Exception as e:
        error_response = ResponseParser.create_error_response(
            "HTTP request processing failed", str(e)
        )
        return ReviewResponseDto(result=error_response)


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
