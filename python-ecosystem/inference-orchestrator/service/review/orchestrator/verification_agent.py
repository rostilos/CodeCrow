import logging
from typing import List, Dict, Any
from langchain_core.tools import tool
from model.output_schemas import CodeReviewIssue
from model.dtos import ReviewRequestDto
from service.review.orchestrator.agents import extract_llm_response_text
from service.review.orchestrator.json_utils import parse_llm_response
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Global state for the tool to access file contents
_FILE_CONTENTS_CACHE: Dict[str, str] = {}

@tool
def search_file_content(file_path: str, search_string: str) -> str:
    """
    Searches for a specific string within the full content of a file.
    Use this tool to verify if a variable, method, or import actually exists in the file.
    
    Args:
        file_path: The path to the file to search in.
        search_string: The exact string to search for (e.g., a variable name or method signature).
        
    Returns:
        A string indicating whether the search_string was found in the file or not.
    """
    content = _FILE_CONTENTS_CACHE.get(file_path)
    if not content:
        return f"Error: File content for '{file_path}' not available in memory."
    
    if search_string in content:
        return f"Found: The string '{search_string}' exists in '{file_path}'."
    else:
        return f"Not Found: The string '{search_string}' does NOT exist in '{file_path}'."

class VerificationResult(BaseModel):
    """Result of the verification agent."""
    issue_ids_to_drop: List[str] = Field(
        description="List of issue IDs that were verified as false positives (e.g., the symbol actually exists)."
    )

async def run_verification_agent(
    llm,
    issues: List[CodeReviewIssue],
    request: ReviewRequestDto
) -> List[CodeReviewIssue]:
    """
    Stage 1.5: LLM-Driven Verification.
    Uses an LLM with a local search tool to verify suspected false positive issues.
    """
    if not request.enrichmentData or not request.enrichmentData.fileContents:
        logger.info("Stage 1.5: No EnrichmentData available, skipping verification.")
        return issues

    # Populate the global cache for the tool
    global _FILE_CONTENTS_CACHE
    _FILE_CONTENTS_CACHE = {
        f.path: f.content for f in request.enrichmentData.fileContents if f.content
    }

    # Filter issues that might be hallucinations
    suspect_categories = {"BUG_RISK", "CODE_QUALITY", "ARCHITECTURE"}
    suspect_issues = []
    safe_issues = []

    for issue in issues:
        if issue.category.upper() not in suspect_categories:
            safe_issues.append(issue)
            continue

        reason_lower = issue.reason.lower()
        is_suspect = any(keyword in reason_lower for keyword in [
            "undefined", "missing import", "not defined", "does not exist",
            "cannot find", "unresolved", "missing property", "missing method"
        ])

        if is_suspect:
            suspect_issues.append(issue)
        else:
            safe_issues.append(issue)

    if not suspect_issues:
        logger.info("Stage 1.5: No suspect issues found, skipping verification.")
        _FILE_CONTENTS_CACHE.clear()
        return issues

    logger.info(f"Stage 1.5: Verifying {len(suspect_issues)} suspect issues...")

    # Prepare the prompt for the verification agent
    issues_json = "\n".join([
        f"ID: {issue.id}\nFile: {issue.file}\nReason: {issue.reason}\n---"
        for issue in suspect_issues
    ])

    prompt = f"""You are a Verification Agent for a code review system.
Your job is to verify if the following issues are false positives caused by "diff-blindness".
Often, a code reviewer only sees the diff and assumes a variable or import is missing, when it actually exists elsewhere in the file.

You have access to a tool called `search_file_content`.
For each issue below, extract the symbol (variable, method, import) that is claimed to be missing.
Use the `search_file_content` tool to check if that symbol actually exists in the specified file.

If the tool returns "Found", the issue is a FALSE POSITIVE and should be dropped.
If the tool returns "Not Found", the issue is REAL and should be kept.

Issues to verify:
{issues_json}

Return a JSON object containing a list of `issue_ids_to_drop` for the issues that are false positives.
"""

    try:
        from langchain.agents import AgentExecutor, create_tool_calling_agent
        from langchain_core.prompts import ChatPromptTemplate

        tools = [search_file_content]
        
        prompt_template = ChatPromptTemplate.from_messages([
            ("system", "You are a helpful verification assistant."),
            ("human", "{input}"),
            ("placeholder", "{agent_scratchpad}"),
        ])

        agent = create_tool_calling_agent(llm, tools, prompt_template)
        agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True)

        # Run the agent
        response = await agent_executor.ainvoke({"input": prompt})
        
        # Parse the output to get the list of IDs to drop
        # We use the structured output parser to ensure we get the right format
        structured_llm = llm.with_structured_output(VerificationResult)
        
        # Ask the LLM to format its own output into the structured schema
        format_prompt = f"Extract the list of issue IDs to drop from this text:\n{response['output']}"
        result = await structured_llm.ainvoke(format_prompt)
        
        ids_to_drop = set(result.issue_ids_to_drop)
        logger.info(f"Stage 1.5: Agent identified {len(ids_to_drop)} false positives to drop.")

        # Filter the suspect issues
        verified_suspect_issues = [
            issue for issue in suspect_issues if issue.id not in ids_to_drop
        ]

        final_issues = safe_issues + verified_suspect_issues
        
    except Exception as e:
        logger.error(f"Stage 1.5 Verification failed: {e}")
        # Fallback: keep all issues if verification fails
        final_issues = issues
    finally:
        # Clean up the cache
        _FILE_CONTENTS_CACHE.clear()

    return final_issues
