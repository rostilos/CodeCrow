"""Parse endpoints — AST metadata extraction without indexing."""
from hashlib import sha256
import logging
from fastapi import APIRouter

from ..models import (
    ParseBatchRequest,
    ParseFileRequest,
    ParsedFileMetadata,
    ParsedRelationship,
    ParsedRepositoryGraphV1,
    ParsedSymbol,
)
from ..symbol_graph import resolve_parsed_repository_graph
from ...models.snapshot import DEFAULT_PARSER_VERSION

logger = logging.getLogger(__name__)
router = APIRouter(tags=["parse"])


def _stable_id(*parts: object) -> str:
    encoded = "\x00".join(str(part) for part in parts).encode("utf-8")
    return sha256(encoded).hexdigest()


def _qualified_name(path: str, namespace: str | None, metadata: dict, name: str) -> str:
    full_path = metadata.get("full_path")
    if isinstance(full_path, str) and full_path:
        return f"{namespace}.{full_path}" if namespace else full_path
    parent_context = metadata.get("parent_context") or []
    local_name = ".".join([*parent_context, name])
    return f"{namespace}.{local_name}" if namespace else local_name or f"{path}:{name}"


def _append_relationships(
    relationships: list[ParsedRelationship],
    *,
    source_symbol_id: str,
    source_name: str,
    source_line: int,
    relationship_type: str,
    targets: object,
) -> None:
    if not isinstance(targets, list):
        return
    for target in targets:
        if not isinstance(target, str) or not target.strip():
            continue
        normalized = target.strip()
        relationship_id = _stable_id(
            source_symbol_id,
            relationship_type,
            normalized,
            source_line,
        )
        relationships.append(ParsedRelationship(
            relationship_id=relationship_id,
            source_symbol_id=source_symbol_id,
            source_name=source_name,
            target_name=normalized,
            relationship_type=relationship_type,
            source_line=source_line,
        ))


@router.post("/parse", response_model=ParsedFileMetadata)
def parse_file(request: ParseFileRequest):
    """
    Parse a single file and extract AST metadata WITHOUT indexing.

    Returns tree-sitter extracted metadata:
    - imports: Import statements
    - extends: Parent classes/interfaces
    - implements: Implemented interfaces
    - semantic_names: Function/class/method names defined
    - namespace: Package/namespace
    - calls: Called functions/methods

    Used by Java pipeline-agent to build dependency graph.
    """
    try:
        from ...core.splitter import ASTCodeSplitter
        from ...core.splitter.languages import (
            EXTENSION_TO_LANGUAGE,
            get_language_from_path,
            get_treesitter_name,
        )

        lang_enum = get_language_from_path(request.path)
        language = request.language
        if not language:
            language = lang_enum.value if lang_enum else None

        if not language:
            ext = '.' + request.path.rsplit('.', 1)[-1] if '.' in request.path else ''
            language = EXTENSION_TO_LANGUAGE.get(ext, {}).get('name')

        splitter = ASTCodeSplitter(
            max_chunk_size=50000,
            enrich_embedding_text=False
        )

        from llama_index.core.schema import Document as LlamaDocument
        doc = LlamaDocument(text=request.content, metadata={'path': request.path})
        nodes = splitter.split_documents([doc])
        content_digest = sha256(request.content.encode("utf-8")).hexdigest()

        imports = set()
        extends = set()
        implements = set()
        semantic_names = set()
        calls = set()
        namespace = None
        parent_classes = set()
        symbols: list[ParsedSymbol] = []
        relationships: list[ParsedRelationship] = []
        seen_symbol_ids: set[str] = set()

        ts_language = get_treesitter_name(lang_enum) if lang_enum else None
        ast_supported = bool(
            ts_language
            and splitter._parser.is_available()
            and splitter._query_runner.has_query(ts_language)
        )

        file_symbol_id = _stable_id(request.path, content_digest, "file")

        for node in nodes:
            meta = node.metadata
            if meta.get('imports'):
                imports.update(meta['imports'])
            if meta.get('extends'):
                extends.update(meta['extends'])
            if meta.get('implements'):
                implements.update(meta['implements'])
            if meta.get('semantic_names'):
                semantic_names.update(meta['semantic_names'])
            if meta.get('calls'):
                calls.update(meta['calls'])
            if meta.get('namespace') and not namespace:
                namespace = meta['namespace']
            if meta.get('parent_class'):
                parent_classes.add(meta['parent_class'])

            primary_name = meta.get("primary_name")
            start_line = int(meta.get("start_line") or 1)
            end_line = max(start_line, int(meta.get("end_line") or start_line))
            if isinstance(primary_name, str) and primary_name.strip():
                name = primary_name.strip()
                qualified_name = _qualified_name(request.path, namespace, meta, name)
                symbol_id = _stable_id(
                    request.path,
                    content_digest,
                    qualified_name,
                    meta.get("node_type") or "code",
                    start_line,
                    end_line,
                )
                if symbol_id not in seen_symbol_ids:
                    seen_symbol_ids.add(symbol_id)
                    parent_context = meta.get("parent_context") or []
                    parent_symbol = parent_context[-1] if parent_context else None
                    extraction_method = (
                        "ast"
                        if meta.get("content_type") == "functions_classes"
                        else "fallback"
                    )
                    symbols.append(ParsedSymbol(
                        symbol_id=symbol_id,
                        path=request.path,
                        name=name,
                        qualified_name=qualified_name,
                        kind=str(meta.get("node_type") or "code"),
                        start_line=start_line,
                        end_line=end_line,
                        parent_symbol=parent_symbol,
                        signature=meta.get("signature"),
                        parameters=list(meta.get("parameters") or []),
                        return_type=meta.get("return_type"),
                        modifiers=list(meta.get("modifiers") or []),
                        decorators=list(meta.get("decorators") or []),
                        extraction_method=extraction_method,
                    ))
                    for relation_type, key in (
                        ("extends", "extends"),
                        ("implements", "implements"),
                        ("calls", "calls"),
                        ("references", "referenced_types"),
                    ):
                        _append_relationships(
                            relationships,
                            source_symbol_id=symbol_id,
                            source_name=qualified_name,
                            source_line=start_line,
                            relationship_type=relation_type,
                            targets=meta.get(key),
                        )
                    if parent_symbol:
                        _append_relationships(
                            relationships,
                            source_symbol_id=symbol_id,
                            source_name=qualified_name,
                            source_line=start_line,
                            relationship_type="contained_by",
                            targets=[parent_symbol],
                        )

        for imported_name in sorted(imports):
            _append_relationships(
                relationships,
                source_symbol_id=file_symbol_id,
                source_name=request.path,
                source_line=1,
                relationship_type="imports",
                targets=[imported_name],
            )

        parent_class = list(parent_classes)[0] if parent_classes else None

        return ParsedFileMetadata(
            path=request.path,
            language=language,
            imports=sorted(list(imports)),
            extends=sorted(list(extends)),
            implements=sorted(list(implements)),
            semantic_names=sorted(list(semantic_names)),
            parent_class=parent_class,
            namespace=namespace,
            calls=sorted(list(calls)),
            content_digest=content_digest,
            parser_version=DEFAULT_PARSER_VERSION,
            ast_supported=ast_supported,
            symbols=symbols,
            relationships=relationships,
            degraded_reason=None if ast_supported else "ast_query_unavailable",
            success=True
        )

    except Exception as e:
        logger.warning(f"Error parsing file {request.path}: {e}")
        return ParsedFileMetadata(
            path=request.path,
            success=False,
            error=str(e)
        )


@router.post("/parse/batch")
def parse_files_batch(request: ParseBatchRequest):
    """Parse multiple files and extract AST metadata in batch."""
    results = []

    for file_req in request.files:
        result = parse_file(file_req)
        results.append(result)

    results, graph = resolve_parsed_repository_graph(results)
    successful = sum(1 for r in results if r.success)
    failed = len(results) - successful

    logger.info(f"Batch parse: {successful} successful, {failed} failed out of {len(results)} files")

    return {
        "results": results,
        "graph": graph,
        "summary": {
            "total": len(results),
            "successful": successful,
            "failed": failed,
            "symbols": len(graph.symbols),
            "resolved_relationships": graph.resolved_count,
            "ambiguous_relationships": graph.ambiguous_count,
            "unresolved_relationships": graph.unresolved_count,
        }
    }
