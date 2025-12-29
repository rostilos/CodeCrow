"""
Helper functions for building prompt sections.
"""
from typing import Any, Dict


def build_legacy_rag_section(rag_context: Dict[str, Any]) -> str:
    """Build legacy RAG section for backward compatibility."""
    rag_section = "\n--- RELEVANT CODE CONTEXT FROM CODEBASE ---\n"
    rag_section += "The following code snippets from the repository are semantically relevant to this PR:\n\n"
    for idx, chunk in enumerate(rag_context.get("relevant_code", [])[:5], 1):
        rag_section += f"Context {idx} (from {chunk.get('metadata', {}).get('path', 'unknown')}):\n"
        rag_section += f"{chunk.get('text', '')}\n\n"
    rag_section += "--- END OF RELEVANT CONTEXT ---\n\n"
    return rag_section


def build_structured_rag_section(
    rag_context: Dict[str, Any],
    max_chunks: int = 5,
    token_budget: int = 4000
) -> str:
    """
    Build a structured RAG section with priority markers.
    
    Args:
        rag_context: RAG query results
        max_chunks: Maximum number of chunks to include
        token_budget: Approximate token budget for RAG section
        
    Returns:
        Formatted RAG section string
    """
    if not rag_context or not rag_context.get("relevant_code"):
        return ""
    
    relevant_code = rag_context.get("relevant_code", [])
    related_files = rag_context.get("related_files", [])
    
    section_parts = []
    section_parts.append("=== RAG CONTEXT: Additional Relevant Code (5% attention) ===")
    section_parts.append(f"Related files discovered: {len(related_files)}")
    section_parts.append("")
    
    current_tokens = 0
    tokens_per_char = 0.25
    
    for idx, chunk in enumerate(relevant_code[:max_chunks], 1):
        chunk_text = chunk.get("text", "")
        chunk_tokens = int(len(chunk_text) * tokens_per_char)
        
        if current_tokens + chunk_tokens > token_budget:
            section_parts.append(f"[Remaining {len(relevant_code) - idx + 1} chunks omitted for token budget]")
            break
        
        chunk_path = chunk.get("metadata", {}).get("path", "unknown")
        chunk_score = chunk.get("score", 0)
        
        section_parts.append(f"### RAG Chunk {idx}: {chunk_path}")
        section_parts.append(f"Relevance: {chunk_score:.3f}")
        section_parts.append("```")
        section_parts.append(chunk_text)
        section_parts.append("```")
        section_parts.append("")
        
        current_tokens += chunk_tokens
    
    section_parts.append("=== END RAG CONTEXT ===")
    return "\n".join(section_parts)


def get_additional_instructions() -> str:
    """
    Get additional instructions for the MCP agent focusing on structured JSON output.
    Note: Curly braces must be doubled to escape them for LangChain's ChatPromptTemplate.

    Returns:
        String with additional instructions for the agent
    """
    return (
        "CRITICAL INSTRUCTIONS:\n"
        "1. You have a LIMITED number of steps (max 120). Plan efficiently - do NOT make unnecessary tool calls.\n"
        "2. After retrieving the diff, analyze it and produce your final JSON response IMMEDIATELY.\n"
        "3. Do NOT retrieve every file individually - use the diff output to identify issues.\n"
        "4. Your FINAL response must be ONLY a valid JSON object with 'comment' and 'issues' fields.\n"
        "5. The 'issues' field MUST be a JSON array [], NOT an object with numeric string keys.\n"
        "6. If you cannot complete the review within your step limit, output your partial findings in JSON format.\n"
        "7. Do NOT include any markdown formatting, explanations, or other text - only the JSON structure.\n"
        "8. STOP making tool calls and produce output once you have enough information to analyze.\n"
        "9. If you encounter errors with MCP tools, proceed with available information and note limitations in the comment field.\n"
        "10. FOLLOW PRIORITY ORDER: Analyze HIGH priority sections FIRST, then MEDIUM, then LOW.\n"
        "11. For LARGE PRs: Focus 60% attention on HIGH priority, 25% on MEDIUM, 15% on LOW/RAG."
    )


def build_context_section(
    structured_context: str = None,
    rag_context: Dict[str, Any] = None
) -> str:
    """
    Build the context section for a prompt.
    
    Args:
        structured_context: Pre-structured context with Lost-in-Middle protection
        rag_context: RAG query results (legacy format)
        
    Returns:
        Formatted context section string
    """
    from .constants import LOST_IN_MIDDLE_INSTRUCTIONS
    
    if structured_context:
        return f"""
{LOST_IN_MIDDLE_INSTRUCTIONS}

{structured_context}
"""
    elif rag_context and rag_context.get("relevant_code"):
        return build_legacy_rag_section(rag_context)
    
    return ""
