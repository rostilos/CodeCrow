"""Parse endpoints — AST metadata extraction without indexing."""
import logging
from fastapi import APIRouter

from ..models import ParseFileRequest, ParseBatchRequest, ParsedFileMetadata

logger = logging.getLogger(__name__)
router = APIRouter(tags=["parse"])


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
        from ...core.splitter.languages import get_language_from_path, EXTENSION_TO_LANGUAGE

        language = request.language
        if not language:
            lang_enum = get_language_from_path(request.path)
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

        imports = set()
        extends = set()
        implements = set()
        semantic_names = set()
        calls = set()
        namespace = None
        parent_classes = set()

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

    successful = sum(1 for r in results if r.success)
    failed = len(results) - successful

    logger.info(f"Batch parse: {successful} successful, {failed} failed out of {len(results)} files")

    return {
        "results": results,
        "summary": {
            "total": len(results),
            "successful": successful,
            "failed": failed
        }
    }
