"""
Context and diff extraction helpers for the review orchestrator.
"""
import hashlib
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Set

from utils.path_identity import (
    normalize_repository_path,
    repository_paths_match,
)

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning("Invalid integer for %s=%r; using %s", name, value, default)
        return default


# Chunk count alone is not a prompt budget: AST chunks and repository
# architecture packets can differ by an order of magnitude in size. Keep exact
# structural context first, but bound the total RAG section deterministically.
RAG_CONTEXT_CHAR_BUDGET = max(
    4_000,
    _env_int("REVIEW_RAG_CONTEXT_CHAR_BUDGET", 32_000),
)
RAG_CONTEXT_CHUNK_CHAR_BUDGET = max(
    1_000,
    _env_int("REVIEW_RAG_CONTEXT_CHUNK_CHAR_BUDGET", 12_000),
)
_PLUGIN_FACT_PREFIX = "Plugin graph facts:\n"


def _render_unique_plugin_fact_prefix(
    text: str,
    metadata: Dict[str, Any],
    match_type: str,
    visible_fact_lines: Set[str],
) -> tuple[str, tuple[str, ...]]:
    """Render file-level graph facts once, outside stored semantic source text.

    Current indexes keep the facts in neutral payload metadata while legacy
    indexes may also prefix every semantic chunk with the same fact text.
    Deterministic retrieval can legitimately return several source chunks from
    one file, but repeating that prefix consumes the section budget and pollutes
    source embeddings without adding proof. Focused architecture packets already
    render their exact attributes and provenance, so they remain untouched.

    The caller commits only complete fact lines that survive the final chunk
    budget. This prevents a truncated or omitted entry from hiding the same
    fact in a later chunk.
    """
    if (
        match_type in {"architecture_relation", "architecture_related"}
        or metadata.get("architecture_key")
    ):
        return text, ()

    source_text = text
    fact_lines: tuple[str, ...]
    if text.startswith(_PLUGIN_FACT_PREFIX):
        prefix, separator, legacy_source_text = text.partition("\n\n")
        fact_lines = tuple(
            line
            for line in prefix.splitlines()[1:]
            if line.strip()
        )
        source_text = legacy_source_text if separator else ""
    else:
        facts = metadata.get("plugin_graph_facts")
        fact_lines = tuple(
            f"[{fact.get('kind', 'relation')}] "
            f"{fact.get('source', '')} {fact.get('relation', '')} "
            f"{fact.get('target', '')}".rstrip()
            for fact in facts or ()
            if isinstance(fact, dict)
        )
    if not fact_lines:
        return text, ()

    unique_lines = tuple(
        line for line in fact_lines if line not in visible_fact_lines
    )
    parts: List[str] = []
    if unique_lines:
        parts.append(
            "Plugin graph facts (deduplicated within this prompt):\n"
            + "\n".join(unique_lines)
        )
    if source_text:
        parts.append(source_text)
    return "\n\n".join(parts), fact_lines


def rag_evidence_id(chunk: Dict[str, Any]) -> str:
    """Return a stable prompt citation ID for one retrieved evidence chunk."""
    metadata = chunk.get("metadata") or {}
    identity = {
        "path": (
            metadata.get("path")
            or chunk.get("path")
            or chunk.get("file_path")
            or ""
        ),
        "matchType": chunk.get("_match_type", ""),
        "architectureKey": metadata.get("architecture_key", ""),
        "text": str(chunk.get("text", chunk.get("content", ""))),
    }
    digest = hashlib.sha256(
        json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:16]
    return f"RAG-{digest}"


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
    deleted_files: Optional[List[str]] = None,
    *,
    max_chars: Optional[int] = None,
    max_chunk_chars: Optional[int] = None,
    current_file_complete_paths: Optional[Set[str]] = None,
    visible_evidence_by_id: Optional[
        Dict[str, tuple[Dict[str, Any], ...]]
    ] = None,
) -> str:
    """
    Format RAG context into a readable string for the prompt using tiered budgeting.
    
    Chunks are classified into three tiers based on their relationship to the
    reviewed code, and each tier has a budget. Unused budget cascades to lower tiers.
    
    Tier 1 — Exact graph relations and structural dependencies:
        Focused repository-architecture facts, their concrete source, definitions,
        and transitive parent types. The character budget remains authoritative;
        the count guard only bounds iteration.
        Budget: up to 64 chunks.
    
    Tier 2 — Direct context (same-class methods, PR-indexed, high-score semantic):
        Code from the same class, recently indexed PR data, or semantically
        very similar code. Important for understanding patterns and conventions.
        Budget: up to 16 chunks plus unused structural slots.
    
    Tier 3 — Broader context (namespace peers, duplication, lower-score semantic):
        Namespace neighbours, potential duplicates, and weaker semantic matches.
        Useful but less critical.
        Budget: up to 8 chunks plus unused higher-tier slots.
    
    Args:
        rag_context: RAG response with code chunks
        relevant_files: Batch files associated with this already-retrieved context.
            This formatter does not perform semantic relevance filtering.
        pr_changed_files: Files modified in the PR - chunks from these may be stale
        deleted_files: Files deleted in the PR - chunks from these are always stale
        max_chars: Optional deterministic total character budget for this section.
        max_chunk_chars: Optional per-chunk text budget.
        current_file_complete_paths: Batch files whose complete post-change source
            is already present in the prompt. Their RAG chunks are redundant and
            are omitted; truncated/unavailable current files are not included here.
    """
    if not rag_context:
        logger.debug("RAG context is empty or None")
        return ""
    
    # Handle both "chunks" and "relevant_code" keys (RAG API uses "relevant_code")
    chunks = rag_context.get("relevant_code", []) or rag_context.get("chunks", [])
    if not chunks:
        logger.debug("No chunks found in RAG context (keys: %s)", list(rag_context.keys()))
        return ""
    
    logger.info(f"Processing {len(chunks)} RAG chunks with tiered budgeting")
    
    # Normalize PR changed files for stale-data detection only
    pr_changed_set = {
        normalize_repository_path(path)
        for path in pr_changed_files or []
        if normalize_repository_path(path)
    }
    
    # Normalize deleted files for filtering (chunks from deleted files are always stale)
    deleted_set = {
        normalize_repository_path(path)
        for path in deleted_files or []
        if normalize_repository_path(path)
    }
    
    # ── Pre-filter: remove stale, deleted, and corrupt chunks ──
    valid_chunks = []
    _seen_content_keys = set()
    skipped_stale = 0
    skipped_deleted = 0
    skipped_redundant_current = 0
    skipped_exact_duplicates = 0
    complete_current_paths = {
        str(path).lstrip("/")
        for path in current_file_complete_paths or set()
        if isinstance(path, str) and path
    }
    
    for chunk in chunks:
        metadata = chunk.get("metadata", {})
        path = metadata.get("path") or chunk.get("path") or chunk.get("file_path", "unknown")
        score = chunk.get("score", chunk.get("relevance_score", 0))
        source = chunk.get("_source", chunk.get("source", ""))
        
        # Skip corrupted chunks
        if not path or path in ("unknown", "None"):
            continue
        
        normalized_chunk_path = normalize_repository_path(path)
        plugin_graph_facts = metadata.get("plugin_graph_facts")
        is_exact_architecture_chunk = (
            chunk.get("_match_type", "") in {
                "architecture_relation",
                "architecture_related",
            }
            or bool(metadata.get("architecture_key"))
            or (
                isinstance(plugin_graph_facts, list)
                and any(
                    isinstance(fact, dict)
                    for fact in plugin_graph_facts
                )
            )
        )
        if complete_current_paths and any(
            repository_paths_match(normalized_chunk_path, complete_path)
            for complete_path in complete_current_paths
        ) and not is_exact_architecture_chunk:
            # Raw source from a complete current file is redundant. Exact
            # architecture facts are not: they are the deterministic proof
            # used to label and validate plugin-governed claims.
            skipped_redundant_current += 1
            continue
        
        # Filter: chunks from deleted files are ALWAYS stale
        if deleted_set:
            is_from_deleted_file = any(
                repository_paths_match(normalized_chunk_path, deleted_path)
                for deleted_path in deleted_set
            )
            if is_from_deleted_file:
                skipped_deleted += 1
                continue
        
        # Architecture packets use a synthetic storage path.  Their real
        # provenance is the exact set of files in ``architecture_paths``.  A
        # base-branch packet that depends on any PR-modified file is stale even
        # though its synthetic path is unchanged, and must never reach the LLM.
        architecture_paths = {
            value for value in metadata.get("architecture_paths", [])
            if isinstance(value, str) and value
        }
        architecture_touches_modified_file = bool(
            pr_changed_set
            and any(
                repository_paths_match(architecture_path, changed_path)
                for architecture_path in architecture_paths
                for changed_path in pr_changed_set
            )
        )

        # Filter stale chunks from PR-modified files
        if pr_changed_set:
            is_from_modified_file = any(
                repository_paths_match(normalized_chunk_path, changed_path)
                for changed_path in pr_changed_set
            )
            
            is_pr_indexed = (source == "pr_indexed")
            is_potentially_stale = chunk.get("_potentially_stale", False)
            
            if architecture_touches_modified_file and not is_pr_indexed:
                skipped_stale += 1
                continue

            if is_from_modified_file and not is_pr_indexed:
                stale_threshold = 0.90 if source == "deterministic" else 0.70
                if score < stale_threshold or is_potentially_stale:
                    skipped_stale += 1
                    continue
        
        text = chunk.get("text", chunk.get("content", ""))
        if not text:
            continue
        
        # Deduplicate only repeated retrieval of the same evidence identity.
        # A basename is not an identity: framework repositories commonly have
        # many meaningful files such as module-local ``etc/di.xml`` documents.
        # Likewise, hashing only a prefix can collapse distinct definitions
        # whose headers are identical. The hard section budget below, rather
        # than lossy cross-path deduplication, remains the prompt-cost boundary.
        _content_key = (
            normalized_chunk_path.replace("\\", "/"),
            str(metadata.get("architecture_key", "")),
            hashlib.sha256(str(text).encode("utf-8")).hexdigest(),
        )
        if _content_key in _seen_content_keys:
            skipped_exact_duplicates += 1
            continue
        _seen_content_keys.add(_content_key)
        
        valid_chunks.append(chunk)
    
    if not valid_chunks:
        logger.warning(
            "No RAG chunks passed pre-filter (total: %d, skipped_stale: %d, "
            "skipped_deleted: %d, skipped_redundant_current: %d, "
            "skipped_exact_duplicates: %d)",
            len(chunks),
            skipped_stale,
            skipped_deleted,
            skipped_redundant_current,
            skipped_exact_duplicates,
        )
        return ""

    if skipped_exact_duplicates:
        logger.info(
            "RAG pre-filter omitted %d exact duplicate retrieval result(s)",
            skipped_exact_duplicates,
        )
    
    # ── Classify chunks into tiers ──
    # Exact graph packets are already projected onto facts touching this batch,
    # so a fixed eight-chunk gate would discard valid framework relationships.
    # The hard character cap below is the actual prompt/cost boundary.
    TIER_1_REFERENCE_BUDGET = 64
    TIER_2_BUDGET = 16
    TIER_3_BASE_BUDGET = 8
    
    tier_1 = []  # Structural: definitions, transitive parents
    tier_2 = []  # Direct: changed-file context, class context, PR-indexed, high-score
    tier_3 = []  # Broader: namespace, duplication, lower-score semantic
    
    for chunk in valid_chunks:
        match_type = chunk.get("_match_type", "")
        source = chunk.get("_source", chunk.get("source", ""))
        score = chunk.get("score", chunk.get("relevance_score", 0))
        
        if match_type in (
            "architecture_relation",
            "architecture_related",
            "definition",
            "transitive_parent",
        ):
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
    # Do not apply a second lossy count cap to exact structural evidence.
    # Deterministic retrieval is already count-bounded by Stage 1, and the
    # section character budget below remains the prompt-cost authority. A
    # second slice here silently hid valid graph relations even when the
    # section still had enough room to render them.
    tier_1_selected = tier_1
    tier_1_unused = max(
        0,
        TIER_1_REFERENCE_BUDGET - len(tier_1_selected),
    )
    
    tier_2_effective_budget = TIER_2_BUDGET + tier_1_unused
    tier_2_selected = tier_2[:tier_2_effective_budget]
    tier_2_unused = tier_2_effective_budget - len(tier_2_selected)
    
    tier_3_effective_budget = TIER_3_BASE_BUDGET + tier_2_unused
    tier_3_selected = tier_3[:tier_3_effective_budget]
    
    logger.info(
        f"Tiered assembly: T1={len(tier_1_selected)}/{len(tier_1)} structural, "
        f"T2={len(tier_2_selected)}/{len(tier_2)} direct, "
        f"T3={len(tier_3_selected)}/{len(tier_3)} broader "
        f"(skipped: {skipped_stale} stale, {skipped_deleted} deleted, "
        f"{skipped_redundant_current} redundant-current)"
    )
    
    # ── Format selected chunks in tier order ──
    all_selected = tier_1_selected + tier_2_selected + tier_3_selected
    
    context_char_budget = max(
        1_000,
        max_chars if max_chars is not None else RAG_CONTEXT_CHAR_BUDGET,
    )
    chunk_char_budget = max(
        256,
        max_chunk_chars
        if max_chunk_chars is not None
        else RAG_CONTEXT_CHUNK_CHAR_BUDGET,
    )

    formatted_parts = []
    duplication_parts = []
    included_entry_count = 0
    used_chars = 0
    truncated_chunks = 0
    skipped_for_budget = 0
    visible_plugin_fact_lines: Set[str] = set()
    
    for chunk in all_selected:
        metadata = chunk.get("metadata", {})
        path = metadata.get("path") or chunk.get("path") or chunk.get("file_path", "unknown")
        chunk_type = metadata.get("content_type", metadata.get("type", "code"))
        score = chunk.get("score", chunk.get("relevance_score", 0))
        source = chunk.get("_source", chunk.get("source", ""))
        text = str(chunk.get("text", chunk.get("content", "")))
        text, candidate_plugin_fact_lines = _render_unique_plugin_fact_prefix(
            text,
            metadata,
            str(chunk.get("_match_type", "")),
            visible_plugin_fact_lines,
        )
        
        # Build rich metadata context
        evidence_id = rag_evidence_id(chunk)
        meta_lines = [
            f"Evidence ID: {evidence_id}",
            f"File: {path}",
        ]
        
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
        
        # Separate duplication-source chunks for special formatting. Account
        # for metadata/fences before assigning the remaining space to source
        # text so the returned section never exceeds its declared budget.
        is_duplication = source in ("duplication",)
        entry_prefix = (
            f"### Context from `{path}` (relevance: {score:.2f})\n"
            f"{meta_text}\n"
            "```\n"
        )
        entry_suffix = "\n```\n"
        separator_chars = 2 if included_entry_count else 0
        available_text_chars = min(
            chunk_char_budget,
            context_char_budget
            - used_chars
            - separator_chars
            - len(entry_prefix)
            - len(entry_suffix),
        )
        if available_text_chars < 256:
            skipped_for_budget += 1
            continue

        bounded_text = text
        if len(text) > available_text_chars:
            truncation_marker = (
                "\n[Context chunk truncated by deterministic prompt budget]"
            )
            retained_chars = max(
                1,
                available_text_chars - len(truncation_marker),
            )
            bounded_text = text[:retained_chars].rstrip() + truncation_marker
            truncated_chunks += 1

        formatted_entry = entry_prefix + bounded_text + entry_suffix
        prospective_chars = (
            used_chars + separator_chars + len(formatted_entry)
        )
        if prospective_chars > context_char_budget:
            skipped_for_budget += 1
            continue
        
        if is_duplication:
            duplication_parts.append(formatted_entry)
        else:
            formatted_parts.append(formatted_entry)
        if visible_evidence_by_id is not None:
            # Record every prompt-visible citation ID. Graph facts are optional:
            # generic semantic chunks must remain valid citation sources even
            # when they do not carry plugin-owned deterministic relationships.
            visible_evidence_by_id.setdefault(evidence_id, ())
            facts = metadata.get("plugin_graph_facts")
            if isinstance(facts, list):
                visible_facts = tuple(
                    dict(fact)
                    for fact in facts
                    if isinstance(fact, dict)
                    and all(
                        isinstance(fact.get(field), str)
                        and fact[field]
                        and fact[field] in bounded_text
                        for field in ("kind", "source", "relation", "target")
                    )
                )
                if visible_facts:
                    visible_evidence_by_id[evidence_id] = visible_facts
        visible_plugin_fact_lines.update(
            line
            for line in candidate_plugin_fact_lines
            if line in bounded_text
        )
        included_entry_count += 1
        used_chars = prospective_chars
    
    if not formatted_parts and not duplication_parts:
        logger.warning(f"No RAG chunks included after tiered selection")
        return ""
    
    result_parts = []
    if formatted_parts:
        result_parts.extend(formatted_parts)
    if duplication_parts:
        result_parts.extend(duplication_parts)
    
    result = "\n".join(result_parts)
    logger.info(
        "RAG prompt budget: included=%d/%d chunks, chars=%d/%d, "
        "truncated=%d, omitted_for_budget=%d",
        included_entry_count,
        len(all_selected),
        len(result),
        context_char_budget,
        truncated_chunks,
        skipped_for_budget,
    )
    return result


def format_duplication_context(
    duplication_results: List[Dict[str, Any]],
    batch_file_paths: List[str],
    max_chunks: int = 10,
    visible_evidence_by_id: Optional[
        Dict[str, tuple[Dict[str, Any], ...]]
    ] = None,
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
    batch_paths_set = {
        normalize_repository_path(path)
        for path in batch_file_paths
        if normalize_repository_path(path)
    }
    
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
        if any(
            repository_paths_match(path, batch_path)
            for batch_path in batch_paths_set
        ):
            continue
        
        # Deduplicate only complete identical implementations. Framework and
        # generated files often share long declarations or headers while their
        # executable tails differ; a prefix hash would hide that conflict.
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
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
        evidence_id = rag_evidence_id(item)
        
        meta_lines = [f"Evidence ID: {evidence_id}", f"File: {path}"]
        
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
        if visible_evidence_by_id is not None:
            visible_evidence_by_id.setdefault(evidence_id, ())
            facts = metadata.get("plugin_graph_facts")
            if isinstance(facts, list):
                visible_facts = tuple(
                    dict(fact)
                    for fact in facts
                    if isinstance(fact, dict)
                    and all(
                        isinstance(fact.get(field), str)
                        and fact[field]
                        and fact[field] in text
                        for field in ("kind", "source", "relation", "target")
                    )
                )
                if visible_facts:
                    existing = visible_evidence_by_id.get(evidence_id, ())
                    visible_evidence_by_id[evidence_id] = tuple(sorted(
                        {
                            json.dumps(
                                fact,
                                sort_keys=True,
                                separators=(",", ":"),
                            ): fact
                            for fact in (*existing, *visible_facts)
                        }.values(),
                        key=lambda fact: json.dumps(
                            fact,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    ))
    
    logger.info(f"Formatted {len(filtered)} duplication context chunks for prompt")
    return "\n".join(parts)
