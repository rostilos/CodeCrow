"""
Command API endpoints (summarize, ask).
"""
import logging
from fastapi import APIRouter, Request
from starlette.responses import StreamingResponse

from model.dtos import (
    SummarizeRequestDto, SummarizeResponseDto,
    AskRequestDto, AskResponseDto,
)
from service.command.command_service import CommandService
from api.event_stream import service_event_stream, wants_streaming

router = APIRouter(tags=["commands"])
logger = logging.getLogger(__name__)


def get_command_service(request: Request) -> CommandService:
    """Retrieve the CommandService instance created during app lifespan."""
    return request.app.state.command_service


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
    command_service = get_command_service(request)
    
    try:
        wants_stream = wants_streaming(request)

        if not wants_stream:
            # Non-streaming behavior
            result = await command_service.process_summarize(req)
            return SummarizeResponseDto(
                summary=result.get("summary"),
                diagram=result.get("diagram"),
                diagramType=result.get("diagramType", "MERMAID"),
                error=result.get("error")
            )

        return StreamingResponse(
            service_event_stream(
                lambda callback: command_service.process_summarize(req, event_callback=callback),
                queued_message="summarize request received",
            ),
            media_type="application/x-ndjson",
        )

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
    command_service = get_command_service(request)
    
    try:
        wants_stream = wants_streaming(request)

        if not wants_stream:
            # Non-streaming behavior
            result = await command_service.process_ask(req)
            return AskResponseDto(
                answer=result.get("answer"),
                error=result.get("error")
            )

        return StreamingResponse(
            service_event_stream(
                lambda callback: command_service.process_ask(req, event_callback=callback),
                queued_message="ask request received",
            ),
            media_type="application/x-ndjson",
        )

    except Exception as e:
        return AskResponseDto(error=f"Ask failed: {str(e)}")
