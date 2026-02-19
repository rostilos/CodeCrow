"""
Context and diff extraction helpers for the review orchestrator.
"""
import re
import logging
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


def extract_symbols_from_diff(diff_content: str) -> List[str]:
    """
    Extract potential symbols (identifiers, class names, function names) from diff.
    Used to query cross-file context for related changes.
    """
    if not diff_content:
        return []
    
    # Common language keywords/stop-words to filter out
    STOP_WORDS = {
        # Python
        'import', 'from', 'class', 'def', 'return', 'if', 'else', 'elif',
        'for', 'while', 'try', 'except', 'finally', 'with', 'as', 'pass',
        'break', 'continue', 'raise', 'yield', 'lambda', 'async', 'await',
        'True', 'False', 'None', 'and', 'or', 'not', 'in', 'is',
        # Java/TS/JS
        'public', 'private', 'protected', 'static', 'final', 'void',
        'new', 'this', 'super', 'extends', 'implements', 'interface',
        'abstract', 'const', 'let', 'var', 'function', 'export', 'default',
        'throw', 'throws', 'catch', 'instanceof', 'typeof', 'null',
        # Common
        'true', 'false', 'null', 'undefined', 'self', 'args', 'kwargs',
        'string', 'number', 'boolean', 'object', 'array', 'list', 'dict',
    }
    
    symbols = set()
    
    # Patterns for common identifiers
    # Match CamelCase identifiers (likely class/component names)
    camel_case = re.findall(r'\b([A-Z][a-z]+[A-Z][a-zA-Z]*)\b', diff_content)
    symbols.update(camel_case)
    
    # Match snake_case identifiers (variables, functions)
    snake_case = re.findall(r'\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b', diff_content)
    symbols.update(s for s in snake_case if len(s) > 5)  # Filter short ones
    
    # Match assignments and function calls
    assignments = re.findall(r'\b(\w+)\s*[=:]\s*', diff_content)
    symbols.update(a for a in assignments if len(a) > 3)
    
    # Match import statements
    imports = re.findall(r'(?:from|import)\s+([a-zA-Z_][a-zA-Z0-9_.]+)', diff_content)
    symbols.update(imports)
    
    # Filter out stop-words and return
    filtered = [s for s in symbols if s.lower() not in STOP_WORDS and len(s) > 2]
    return filtered[:20]  # Limit to top 20 symbols


def extract_diff_snippets(diff_content: str) -> List[str]:
    """
    Extract meaningful code snippets from diff content for RAG semantic search.
    Focuses on added/modified lines that represent significant code changes.
    """
    if not diff_content:
        return []
    
    snippets = []
    current_snippet_lines = []
    
    for line in diff_content.splitlines():
        # Focus on added lines (new code)
        if line.startswith("+") and not line.startswith("+++"):
            clean_line = line[1:].strip()
            # Skip trivial lines
            if (clean_line and 
                len(clean_line) > 10 and  # Minimum meaningful length
                not clean_line.startswith("//") and  # Skip comments
                not clean_line.startswith("#") and
                not clean_line.startswith("*") and
                not clean_line == "{" and
                not clean_line == "}" and
                not clean_line == ""):
                current_snippet_lines.append(clean_line)
                
                # Batch into snippets of 3-5 lines
                if len(current_snippet_lines) >= 3:
                    snippets.append(" ".join(current_snippet_lines))
                    current_snippet_lines = []
    
    # Add remaining lines as final snippet
    if current_snippet_lines:
        snippets.append(" ".join(current_snippet_lines))
    
    # Limit to most significant snippets
    return snippets[:10]


def get_diff_snippets_for_batch(
    all_diff_snippets: List[str], 
    batch_file_paths: List[str]
) -> List[str]:
    """
    Filter diff snippets to only include those relevant to the batch files.
    
    Note: Java DiffParser.extractDiffSnippets() returns CLEAN CODE SNIPPETS (no file paths).
    These snippets are just significant code lines like function signatures.
    Since snippets don't contain file paths, we return all snippets for semantic search.
    The embedding similarity will naturally prioritize relevant matches.
    """
    if not all_diff_snippets:
        return []
    
    # Java snippets are clean code (no file paths), so we can't filter by path
    # Return all snippets - the semantic search will find relevant matches
    logger.info(f"Using {len(all_diff_snippets)} diff snippets for batch files {batch_file_paths}")
    return all_diff_snippets


def format_rag_context(
    rag_context: Optional[Dict[str, Any]], 
    relevant_files: Optional[Set[str]] = None,
    pr_changed_files: Optional[List[str]] = None,
    deleted_files: Optional[List[str]] = None
) -> str:
    """
    Format RAG context into a readable string for the prompt.
    
    IMPORTANT: We trust RAG's semantic similarity scores for relevance.
    The RAG system already uses embeddings to find semantically related code.
    We only filter out chunks from files being modified in the PR (stale data from main branch).
    
    Args:
        rag_context: RAG response with code chunks
        relevant_files: (UNUSED - kept for API compatibility) - we trust RAG scores instead
        pr_changed_files: Files modified in the PR - chunks from these may be stale
    """
    if not rag_context:
        logger.debug("RAG context is empty or None")
        return ""
    
    # Handle both "chunks" and "relevant_code" keys (RAG API uses "relevant_code")
    chunks = rag_context.get("relevant_code", []) or rag_context.get("chunks", [])
    if not chunks:
        logger.debug("No chunks found in RAG context (keys: %s)", list(rag_context.keys()))
        return ""
    
    logger.info(f"Processing {len(chunks)} RAG chunks (trusting semantic similarity scores)")
    
    # Normalize PR changed files for stale-data detection only
    pr_changed_set = set()
    if pr_changed_files:
        for f in pr_changed_files:
            pr_changed_set.add(f)
            if "/" in f:
                pr_changed_set.add(f.rsplit("/", 1)[-1])
    
    # Normalize deleted files for filtering (chunks from deleted files are always stale)
    deleted_set = set()
    if deleted_files:
        for f in deleted_files:
            deleted_set.add(f)
            if "/" in f:
                deleted_set.add(f.rsplit("/", 1)[-1])
    
    formatted_parts = []
    duplication_parts = []
    included_count = 0
    skipped_stale = 0
    skipped_deleted = 0
    
    for chunk in chunks:
        if included_count >= 20:
            logger.debug(f"Reached chunk limit of 20")
            break
            
        metadata = chunk.get("metadata", {})
        # Support both 'path' and 'file_path' keys (deterministic uses file_path)
        path = metadata.get("path") or chunk.get("path") or chunk.get("file_path", "unknown")
        chunk_type = metadata.get("content_type", metadata.get("type", "code"))
        score = chunk.get("score", chunk.get("relevance_score", 0))
        source = chunk.get("_source", chunk.get("source", ""))
        
        # Filter: chunks from deleted files are ALWAYS stale (file is being removed in this PR)
        if deleted_set:
            path_filename = path.rsplit("/", 1)[-1] if "/" in path else path
            is_from_deleted_file = (
                path in deleted_set or 
                path_filename in deleted_set or
                any(path.endswith(f) or f.endswith(path) for f in deleted_set)
            )
            if is_from_deleted_file:
                logger.debug(f"Skipping chunk from DELETED file: {path} (score={score:.2f})")
                skipped_deleted += 1
                continue
        
        # Only filter: chunks from PR-modified files with LOW scores (likely stale)
        # High-score chunks from modified files may still be relevant (other parts of same file)
        # EXCEPTION: PR-indexed chunks are NEVER stale — they come from the actual PR
        if pr_changed_set:
            path_filename = path.rsplit("/", 1)[-1] if "/" in path else path
            is_from_modified_file = (
                path in pr_changed_set or 
                path_filename in pr_changed_set or
                any(path.endswith(f) or f.endswith(path) for f in pr_changed_set)
            )
            
            is_pr_indexed = (source == "pr_indexed")
            is_potentially_stale = chunk.get("_potentially_stale", False)
            
            if is_from_modified_file and not is_pr_indexed:
                # For deterministic chunks from modified files, use a HIGHER threshold
                # because they're assigned score=0.85 which bypasses the normal 0.70 threshold
                stale_threshold = 0.90 if source == "deterministic" else 0.70
                
                if score < stale_threshold or is_potentially_stale:
                    logger.debug(f"Skipping stale chunk from modified file: {path} "
                                f"(score={score:.2f}, source={source}, threshold={stale_threshold})")
                    skipped_stale += 1
                    continue
        
        text = chunk.get("text", chunk.get("content", ""))
        if not text:
            continue
        
        included_count += 1
        
        # Build rich metadata context
        meta_lines = [f"File: {path}"]
        
        if metadata.get("namespace"):
            meta_lines.append(f"Namespace: {metadata['namespace']}")
        elif metadata.get("package"):
            meta_lines.append(f"Package: {metadata['package']}")
        
        if metadata.get("primary_name"):
            meta_lines.append(f"Definition: {metadata['primary_name']}")
        elif metadata.get("semantic_names"):
            meta_lines.append(f"Definitions: {', '.join(metadata['semantic_names'][:5])}")
        
        if metadata.get("extends"):
            extends = metadata["extends"]
            meta_lines.append(f"Extends: {', '.join(extends) if isinstance(extends, list) else extends}")
        
        if metadata.get("implements"):
            implements = metadata["implements"]
            meta_lines.append(f"Implements: {', '.join(implements) if isinstance(implements, list) else implements}")
        
        if metadata.get("imports"):
            imports = metadata["imports"]
            if isinstance(imports, list):
                if len(imports) <= 5:
                    meta_lines.append(f"Imports: {'; '.join(imports)}")
                else:
                    meta_lines.append(f"Imports: {'; '.join(imports[:5])}... (+{len(imports)-5} more)")
        
        if metadata.get("parent_context"):
            parent_ctx = metadata["parent_context"]
            if isinstance(parent_ctx, list):
                meta_lines.append(f"Parent: {'.'.join(parent_ctx)}")
        
        if chunk_type and chunk_type != "code":
            meta_lines.append(f"Type: {chunk_type}")
        
        meta_text = "\n".join(meta_lines)
        
        # Separate duplication-source chunks for special formatting
        is_duplication = source in ("duplication", "deterministic")
        formatted_entry = (
            f"### Context from `{path}` (relevance: {score:.2f})\n"
            f"{meta_text}\n"
            f"```\n{text}\n```\n"
        )
        
        if is_duplication:
            duplication_parts.append(formatted_entry)
        else:
            formatted_parts.append(formatted_entry)
    
    if not formatted_parts and not duplication_parts:
        logger.warning(f"No RAG chunks included (total: {len(chunks)}, skipped_stale: {skipped_stale})")
        return ""
    
    logger.info(f"Included {len(formatted_parts) + len(duplication_parts)} RAG chunks "
                f"({len(duplication_parts)} from duplication search, "
                f"skipped {skipped_stale} stale from modified files, "
                f"skipped {skipped_deleted} from deleted files)")
    
    result_parts = []
    if formatted_parts:
        result_parts.extend(formatted_parts)
    if duplication_parts:
        result_parts.extend(duplication_parts)
    
    return "\n".join(result_parts)


def format_duplication_context(
    duplication_results: List[Dict[str, Any]],
    batch_file_paths: List[str],
    max_chunks: int = 10
) -> str:
    """
    Format duplication search results into a dedicated context section
    for the LLM prompt. This is separate from the general RAG context
    and specifically highlights code that may be doing the same thing
    as the code under review.
    
    Args:
        duplication_results: Results from duplication-oriented semantic search
        batch_file_paths: Files being reviewed in this batch (to exclude self-matches)
        max_chunks: Maximum chunks to include
    
    Returns:
        Formatted string for prompt inclusion, or empty string if no results
    """
    if not duplication_results:
        return ""
    
    # Normalize batch paths for filtering
    batch_basenames = set()
    batch_paths_set = set()
    for p in batch_file_paths:
        batch_paths_set.add(p)
        if "/" in p:
            batch_basenames.add(p.rsplit("/", 1)[-1])
    
    # Filter out self-matches and deduplicate
    seen_texts = set()
    filtered = []
    
    for result in duplication_results:
        metadata = result.get("metadata", {})
        path = metadata.get("path", metadata.get("file_path", result.get("path", "")))
        text = result.get("text", result.get("content", ""))
        score = result.get("score", 0)
        
        if not text or score < 0.65:
            continue
        
        # Skip chunks from the files being reviewed (self-matches)
        path_basename = path.rsplit("/", 1)[-1] if "/" in path else path
        if path in batch_paths_set or path_basename in batch_basenames:
            continue
        
        # Deduplicate by content
        text_hash = hash(text[:200])
        if text_hash in seen_texts:
            continue
        seen_texts.add(text_hash)
        
        filtered.append({
            "path": path,
            "text": text,
            "score": score,
            "metadata": metadata,
            "query": result.get("_query", "")
        })
    
    if not filtered:
        return ""
    
    # Sort by score descending and limit
    filtered.sort(key=lambda x: x["score"], reverse=True)
    filtered = filtered[:max_chunks]
    
    parts = []
    parts.append("⚠️ EXISTING SIMILAR IMPLEMENTATIONS FOUND IN CODEBASE:")
    parts.append("The following code already exists elsewhere and may implement the SAME functionality:")
    parts.append("")
    
    for i, item in enumerate(filtered, 1):
        path = item["path"]
        score = item["score"]
        text = item["text"]
        metadata = item.get("metadata", {})
        
        meta_lines = [f"File: {path}"]
        
        if metadata.get("namespace"):
            meta_lines.append(f"Namespace: {metadata['namespace']}")
        if metadata.get("primary_name"):
            meta_lines.append(f"Definition: {metadata['primary_name']}")
        if metadata.get("extends"):
            extends = metadata["extends"]
            meta_lines.append(f"Extends: {', '.join(extends) if isinstance(extends, list) else extends}")
        
        meta_text = "\n".join(meta_lines)
        
        parts.append(
            f"### Existing Implementation #{i} from `{path}` (similarity: {score:.2f})\n"
            f"{meta_text}\n"
            f"```\n{text}\n```\n"
        )
    
    logger.info(f"Formatted {len(filtered)} duplication context chunks for prompt")
    return "\n".join(parts)
