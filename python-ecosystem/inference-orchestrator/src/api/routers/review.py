"""
Review API endpoints.
"""
from fastapi import APIRouter, Request
from starlette.responses import StreamingResponse

from model.dtos import ReviewRequestDto, ReviewResponseDto
from service.review.review_service import ReviewService
from utils.error_sanitizer import create_error_response
from api.event_stream import service_event_stream, wants_streaming

router = APIRouter(tags=["review"])


def get_review_service(request: Request) -> ReviewService:
    """Retrieve the ReviewService instance created during app lifespan."""
    return request.app.state.review_service


import logging

logger = logging.getLogger(__name__)


@router.post("/review", response_model=ReviewResponseDto, deprecated=True)
async def review_endpoint(req: ReviewRequestDto, request: Request):
    """
    [DEPRECATED] HTTP endpoint to accept review requests from the pipeline agent.
    
    Please use the Redis Queue asynchronous message broker instead (codecrow:analysis:jobs).


    Behavior:
    - If the client requests streaming via header `Accept: application/x-ndjson`,
      the endpoint will return a StreamingResponse that yields NDJSON events as they occur.
    - Otherwise it preserves the original behavior and returns a single ReviewResponseDto JSON body.
    """
    review_service = get_review_service(request)
    
    try:
        wants_stream = wants_streaming(request)

        if not wants_stream:
            # Non-streaming (legacy) behavior
            result = await review_service.process_review_request(req)
            return ReviewResponseDto(
                result=result.get("result"),
                error=result.get("error")
            )

        return StreamingResponse(
            service_event_stream(
                lambda callback: review_service.process_review_request(req, event_callback=callback),
                queued_message="request received",
                result_value=lambda result: result.get("result"),
            ),
            media_type="application/x-ndjson",
        )

    except Exception as e:
        error_response = create_error_response(
            "HTTP request processing failed", str(e)
        )
        return ReviewResponseDto(result=error_response)
