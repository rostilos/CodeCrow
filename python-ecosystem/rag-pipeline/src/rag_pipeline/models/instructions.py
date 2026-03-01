from enum import Enum
from typing import Dict, Optional


class InstructionType(str, Enum):
    DEPENDENCY = "dependency"
    LOGIC = "logic"
    IMPACT = "impact"
    GENERAL = "general"
    DUPLICATION = "duplication"


# Instruction-aware embedding models use a structured format to improve
# retrieval quality. Each instruction tells the model HOW to interpret the
# query, resulting in better-aligned embeddings.
#
# Models that support this:
#   - Qwen3-Embedding (any size) — "Instruct: {instruction}\nQuery: {query}"
#   - Instructor variants — similar format
#   - BGE-EN-ICL — "Represent this sentence for searching relevant passages: {query}"
#
# Models that do NOT support this (instructions become noise):
#   - OpenAI text-embedding-3-* — no instruction support
#   - Cohere embed-* — uses its own input_type parameter
#   - nomic-embed-text — uses "search_query:" / "search_document:" prefixes

INSTRUCTIONS: Dict[InstructionType, str] = {
    InstructionType.DEPENDENCY: (
        "Given the following function name or signature, retrieve all code snippets "
        "that invoke this logic or depend on its return value across the repository."
    ),
    InstructionType.LOGIC: (
        "Retrieve code snippets that implement the same business logic or data schema, "
        "regardless of the programming language used."
    ),
    InstructionType.IMPACT: (
        "Find all downstream components, interfaces, or configurations that would "
        "be affected by a change in the following implementation."
    ),
    InstructionType.GENERAL: (
        "Given a web search query, retrieve relevant passages that answer the query"
    ),
    InstructionType.DUPLICATION: (
        "Given the following code implementation, retrieve all existing code snippets "
        "in the repository that implement the same or very similar functionality, "
        "serve the same purpose, or operate on the same hooks, events, or extension "
        "points, even if they use different class names, approaches, or are located "
        "in different modules."
    ),
}


def format_query(
    query: str,
    instruction_type: InstructionType = InstructionType.GENERAL,
    supports_instructions: bool = True,
) -> str:
    """
    Format a query with optional instruction prefix for instruction-aware
    embedding models.

    When ``supports_instructions`` is True (default), the query is wrapped in
    the ``Instruct: … / Query: …`` format understood by models like Qwen3-Embedding.

    When False, the raw query is returned as-is. This avoids injecting
    instruction text into the embedding for standard models (OpenAI, Cohere,
    etc.) where it would act as noise and degrade retrieval quality.

    Args:
        query: The raw search query.
        instruction_type: Which instruction to prepend.
        supports_instructions: Whether the active embedding model supports
            instruction-prefixed queries. Read from ``RAGConfig.embedding_supports_instructions``.

    Returns:
        The formatted (or raw) query string ready for embedding.
    """
    if not supports_instructions:
        return query

    instruction = INSTRUCTIONS.get(instruction_type, INSTRUCTIONS[InstructionType.GENERAL])
    return f"Instruct: {instruction}\nQuery: {query}"
