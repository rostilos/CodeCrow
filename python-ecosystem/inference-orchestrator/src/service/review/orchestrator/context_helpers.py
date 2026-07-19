"""
Context and diff extraction helpers for the review orchestrator.
"""
import re
import logging
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


def format_related_context_pack(pack: Dict[str, Any]) -> str:
    """Render provenance-bearing context without turning leads into proof."""
    if not isinstance(pack, dict):
        return ""

    receipt = pack.get("receipt") or {}
    anchors = pack.get("anchors") or []
    items = pack.get("items") or []
    gaps = pack.get("gaps") or []

    parts = [
        "## RELATED CONTEXT PACK V1",
        (
            "Evidence policy: related items are investigation leads. Only exact-source "
            "items are direct source evidence; structural and semantic leads must be "
            "confirmed against the changed code before reporting an issue."
        ),
    ]
    if receipt:
        parts.extend([
            f"Snapshot receipt: {receipt.get('snapshot_id', 'unknown')}",
            (
                f"Coordinates: base={receipt.get('base_sha', 'unknown')} "
                f"head={receipt.get('head_sha', 'unknown')} "
                f"merge-base={receipt.get('merge_base_sha', 'unknown')}"
            ),
            (
                "Processing identity: "
                f"parser={receipt.get('parser_version', 'unknown')}, "
                f"chunker={receipt.get('chunker_version', 'unknown')}, "
                f"embedding={receipt.get('embedding_version', 'unknown')}"
            ),
        ])
    if anchors:
        parts.append(
            "Review anchors: "
            + ", ".join(str(anchor.get("path", "unknown")) for anchor in anchors)
        )

    for index, item in enumerate(items, 1):
        line_range = ""
        if item.get("start_line"):
            line_range = f":{item['start_line']}"
            if item.get("end_line") and item["end_line"] != item["start_line"]:
                line_range += f"-{item['end_line']}"
        revision = item.get("revision") or "unversioned"
        parts.extend([
            f"### Related item {index}: `{item.get('path', 'unknown')}{line_range}`",
            (
                f"Receipt: revision={revision}; digest={item.get('content_digest') or 'unverified'}; "
                f"verified={str(bool(item.get('snapshot_verified'))).lower()}"
            ),
            (
                f"Relationship: {item.get('relationship_type', 'unknown')} "
                f"({item.get('direction', 'unknown')}); retrieval={item.get('retrieval_method', 'unknown')}; "
                f"strength={item.get('evidence_strength', 'unknown')}; "
                f"score={float(item.get('score') or 0):.2f}"
            ),
            f"Why selected: {item.get('selection_reason', 'unspecified')}",
        ])
        if item.get("symbol"):
            parts.append(f"Symbol: {item['symbol']}")
        parts.append(f"```\n{item.get('content', '')}\n```")

    if gaps:
        parts.append("### Context gaps")
        for gap in gaps:
            affected = gap.get("affected_paths") or []
            suffix = f" Affected: {', '.join(affected)}." if affected else ""
            parts.append(
                f"- {gap.get('code', 'unknown')}: {gap.get('detail', '')}{suffix}"
            )

    return "\n".join(parts)


def extract_symbols_from_diff(diff_content: str) -> List[str]:
    """
    Extract neutral identifier-like tokens from diff text for compatibility.

    This helper no longer filters language keywords or assigns semantic
    importance. Callers should treat the result as raw retrieval hints only.
    """
    if not diff_content:
        return []

    tokens = re.findall(r'\b([A-Za-z_][A-Za-z0-9_.]{1,})\b', diff_content)
    return list(dict.fromkeys(tokens))[:20]


def extract_diff_snippets(diff_content: str) -> List[str]:
    """
    Extract added diff lines for RAG semantic search.

    This is intentionally neutral: it does not skip comments, braces, keywords,
    or short lines. Retrieval/reranking and the LLM decide usefulness.
    """
    if not diff_content:
        return []

    snippets = []
    current_snippet_lines = []

    for line in diff_content.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            current_snippet_lines.append(line[1:])
            if len(current_snippet_lines) >= 8:
                snippets.append("\n".join(current_snippet_lines))
                current_snippet_lines = []

    if current_snippet_lines:
        snippets.append("\n".join(current_snippet_lines))

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
    Format RAG context into a readable string for the prompt using tiered budgeting.
    
    Chunks are classified into three tiers based on their relationship to the
    reviewed code, and each tier has a budget. Unused budget cascades to lower tiers.
    
    Tier 1 — Structural dependencies (extends, implements, parent types):
        Definitions and transitive parent types that the reviewed code directly
        depends on. These are critical for understanding correctness.
        Budget: 8 chunks.
    
    Tier 2 — Direct context (same-class methods, PR-indexed, high-score semantic):
        Code from the same class, recently indexed PR data, or semantically
        very similar code. Important for understanding patterns and conventions.
        Budget: 8 chunks.
    
    Tier 3 — Broader context (namespace peers, duplication, lower-score semantic):
        Namespace neighbours, potential duplicates, and weaker semantic matches.
        Useful but less critical.
        Budget: 4 chunks + unused from Tier 1 & 2.
    
    Args:
        rag_context: RAG response with code chunks
        relevant_files: Batch files associated with this already-retrieved context.
            This formatter does not perform semantic relevance filtering.
        pr_changed_files: Files modified in the PR - chunks from these may be stale
        deleted_files: Files deleted in the PR - chunks from these are always stale
    """
    if not rag_context:
        logger.debug("RAG context is empty or None")
        return ""

    structured_pack = rag_context.get("related_context_pack_v1")
    if isinstance(structured_pack, dict):
        return format_related_context_pack(structured_pack)
    
    # Handle both "chunks" and "relevant_code" keys (RAG API uses "relevant_code")
    chunks = rag_context.get("relevant_code", []) or rag_context.get("chunks", [])
    if not chunks:
        logger.debug("No chunks found in RAG context (keys: %s)", list(rag_context.keys()))
        return ""
    
    logger.info(f"Processing {len(chunks)} RAG chunks with tiered budgeting")
    
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
    
    # ── Pre-filter: remove stale, deleted, and corrupt chunks ──
    valid_chunks = []
    _seen_content_keys = set()
    skipped_stale = 0
    skipped_deleted = 0
    
    for chunk in chunks:
        metadata = chunk.get("metadata", {})
        path = metadata.get("path") or chunk.get("path") or chunk.get("file_path", "unknown")
        score = chunk.get("score", chunk.get("relevance_score", 0))
        source = chunk.get("_source", chunk.get("source", ""))
        
        # Skip corrupted chunks
        if not path or path in ("unknown", "None"):
            continue
        
        _norm_path = path
        
        # Filter: chunks from deleted files are ALWAYS stale
        if deleted_set:
            path_filename = _norm_path.rsplit("/", 1)[-1] if "/" in _norm_path else _norm_path
            is_from_deleted_file = (
                _norm_path in deleted_set or 
                path_filename in deleted_set or
                any(_norm_path.endswith(f) or f.endswith(_norm_path) for f in deleted_set)
            )
            if is_from_deleted_file:
                skipped_deleted += 1
                continue
        
        # Filter stale chunks from PR-modified files
        if pr_changed_set:
            path_filename = _norm_path.rsplit("/", 1)[-1] if "/" in _norm_path else _norm_path
            is_from_modified_file = (
                _norm_path in pr_changed_set or 
                path_filename in pr_changed_set or
                any(_norm_path.endswith(f) or f.endswith(_norm_path) for f in pr_changed_set)
            )
            
            is_pr_indexed = (source == "pr_indexed")
            is_potentially_stale = chunk.get("_potentially_stale", False)
            
            if is_from_modified_file and not is_pr_indexed:
                stale_threshold = 0.90 if source == "deterministic" else 0.70
                if score < stale_threshold or is_potentially_stale:
                    skipped_stale += 1
                    continue
        
        text = chunk.get("text", chunk.get("content", ""))
        if not text:
            continue
        
        # Deduplicate
        _dedup_basename = _norm_path.rsplit("/", 1)[-1] if "/" in _norm_path else _norm_path
        _content_key = (_dedup_basename, hash(text[:300]))
        if _content_key in _seen_content_keys:
            continue
        _seen_content_keys.add(_content_key)
        
        valid_chunks.append(chunk)
    
    if not valid_chunks:
        logger.warning(f"No RAG chunks passed pre-filter (total: {len(chunks)}, "
                       f"skipped_stale: {skipped_stale}, skipped_deleted: {skipped_deleted})")
        return ""
    
    # ── Classify chunks into tiers ──
    TIER_1_BUDGET = 8   # Structural dependencies
    TIER_2_BUDGET = 8   # Direct context
    TIER_3_BASE_BUDGET = 4  # Broader context (+ cascade)
    
    tier_1 = []  # Structural: definitions, transitive parents
    tier_2 = []  # Direct: changed-file context, class context, PR-indexed, high-score
    tier_3 = []  # Broader: namespace, duplication, lower-score semantic
    
    for chunk in valid_chunks:
        match_type = chunk.get("_match_type", "")
        source = chunk.get("_source", chunk.get("source", ""))
        score = chunk.get("score", chunk.get("relevance_score", 0))
        
        if match_type in ("definition", "transitive_parent"):
            # Tier 1: type definitions the reviewed code depends on
            tier_1.append(chunk)
        elif match_type in ("changed_file", "class_context") or source == "pr_indexed":
            # Tier 2: same-class and PR-indexed context
            tier_2.append(chunk)
        elif source == "duplication":
            # Tier 3: duplication matches are useful but not critical
            tier_3.append(chunk)
        elif match_type == "namespace_context":
            # Tier 3: namespace siblings
            tier_3.append(chunk)
        elif score >= 0.88:
            # Tier 2: only genuinely high-confidence semantic matches
            tier_2.append(chunk)
        else:
            # Tier 3: everything else
            tier_3.append(chunk)
    
    # Apply budgets with cascade
    tier_1_selected = tier_1[:TIER_1_BUDGET]
    tier_1_unused = TIER_1_BUDGET - len(tier_1_selected)
    
    tier_2_effective_budget = TIER_2_BUDGET + tier_1_unused
    tier_2_selected = tier_2[:tier_2_effective_budget]
    tier_2_unused = tier_2_effective_budget - len(tier_2_selected)
    
    tier_3_effective_budget = TIER_3_BASE_BUDGET + tier_2_unused
    tier_3_selected = tier_3[:tier_3_effective_budget]
    
    logger.info(
        f"Tiered assembly: T1={len(tier_1_selected)}/{len(tier_1)} structural, "
        f"T2={len(tier_2_selected)}/{len(tier_2)} direct, "
        f"T3={len(tier_3_selected)}/{len(tier_3)} broader "
        f"(skipped: {skipped_stale} stale, {skipped_deleted} deleted)"
    )
    
    # ── Format selected chunks in tier order ──
    all_selected = tier_1_selected + tier_2_selected + tier_3_selected
    
    formatted_parts = []
    duplication_parts = []
    
    for chunk in all_selected:
        metadata = chunk.get("metadata", {})
        path = metadata.get("path") or chunk.get("path") or chunk.get("file_path", "unknown")
        chunk_type = metadata.get("content_type", metadata.get("type", "code"))
        score = chunk.get("score", chunk.get("relevance_score", 0))
        source = chunk.get("_source", chunk.get("source", ""))
        text = chunk.get("text", chunk.get("content", ""))
        
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
        is_duplication = source in ("duplication",)
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
        logger.warning(f"No RAG chunks included after tiered selection")
        return ""
    
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
