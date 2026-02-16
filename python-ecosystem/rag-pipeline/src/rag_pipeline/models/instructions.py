from enum import Enum
from typing import Dict

class InstructionType(str, Enum):
    DEPENDENCY = "dependency"
    LOGIC = "logic"
    IMPACT = "impact"
    GENERAL = "general"
    DUPLICATION = "duplication"

# Instructions for Qwen3-Embedding-8B
# Source: https://qwenlm.github.io/blog/qwen3-embedding/
INSTRUCTIONS: Dict[InstructionType, str] = {
    InstructionType.DEPENDENCY: "Given the following function name or signature, retrieve all code snippets that invoke this logic or depend on its return value across the repository.",
    InstructionType.LOGIC: "Retrieve code snippets that implement the same business logic or data schema, regardless of the programming language used.",
    InstructionType.IMPACT: "Find all downstream components, interfaces, or configurations that would be affected by a change in the following implementation.",
    InstructionType.GENERAL: "Given a web search query, retrieve relevant passages that answer the query",
    InstructionType.DUPLICATION: "Given the following code implementation, retrieve all existing code snippets in the repository that implement the same or very similar functionality, serve the same purpose, or operate on the same hooks, events, or extension points, even if they use different class names, approaches, or are located in different modules."
}

def format_query(query: str, instruction_type: InstructionType = InstructionType.GENERAL) -> str:
    """
    Format a query with the appropriate instruction for Qwen3-Embedding.
    
    Format: "Instruct: {instruction}\nQuery: {query}"
    """
    instruction = INSTRUCTIONS.get(instruction_type, INSTRUCTIONS[InstructionType.GENERAL])
    return f"Instruct: {instruction}\nQuery: {query}"
