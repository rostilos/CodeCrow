from typing import List
import uuid
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import Document, TextNode
from ..utils.utils import is_code_file


class CodeAwareSplitter:
    """
    Code-aware text splitter that handles code and text differently.

    DEPRECATED: Use SemanticCodeSplitter instead, which provides:
    - Full AST-aware parsing for multiple languages
    - Better metadata extraction (docstrings, signatures, imports)
    - Smarter chunk merging and boundary detection

    This class just wraps SentenceSplitter with different chunk sizes for
    code vs text. For truly semantic code splitting, use SemanticCodeSplitter.
    """

    def __init__(self, code_chunk_size: int = 800, code_overlap: int = 200,
                 text_chunk_size: int = 1000, text_overlap: int = 200):
        self.code_splitter = SentenceSplitter(
            chunk_size=code_chunk_size,
            chunk_overlap=code_overlap,
            separator="\n\n",
        )

        self.text_splitter = SentenceSplitter(
            chunk_size=text_chunk_size,
            chunk_overlap=text_overlap,
        )

    def split_documents(self, documents: List[Document]) -> List[TextNode]:
        """Split documents into chunks based on their language type"""
        result = []

        for doc in documents:
            language = doc.metadata.get("language", "text")
            is_code = is_code_file(language)

            splitter = self.code_splitter if is_code else self.text_splitter

            nodes = splitter.get_nodes_from_documents([doc])

            for i, node in enumerate(nodes):
                # Skip empty or whitespace-only chunks
                if not node.text or not node.text.strip():
                    continue

                # Truncate text if too large (>30k chars ≈ 7.5k tokens)
                text = node.text
                if len(text) > 30000:
                    text = text[:30000]

                metadata = dict(doc.metadata)
                metadata["chunk_index"] = i
                metadata["total_chunks"] = len(nodes)

                # Create TextNode with explicit UUID
                chunk_node = TextNode(
                    id_=str(uuid.uuid4()),
                    text=text,
                    metadata=metadata
                )
                result.append(chunk_node)

        return result

    def split_text_for_language(self, text: str, language: str) -> List[str]:
        """Split text based on language type"""
        is_code = is_code_file(language)
        splitter = self.code_splitter if is_code else self.text_splitter

        temp_doc = Document(text=text, metadata={"language": language})
        nodes = splitter.get_nodes_from_documents([temp_doc])

        return [node.text for node in nodes]


class FunctionAwareSplitter:
    """
    Advanced splitter that tries to preserve function boundaries.

    DEPRECATED: Use SemanticCodeSplitter instead, which provides:
    - Full AST-aware parsing for multiple languages
    - Better metadata extraction (docstrings, signatures, imports)
    - Smarter chunk merging and boundary detection

    This class is kept for backward compatibility only.
    """

    def __init__(self, max_chunk_size: int = 800, overlap: int = 200):
        self.max_chunk_size = max_chunk_size
        self.overlap = overlap
        self.fallback_splitter = SentenceSplitter(
            chunk_size=max_chunk_size,
            chunk_overlap=overlap,
        )

    def split_by_functions(self, text: str, language: str) -> List[str]:
        """Try to split code by functions/classes"""

        if language == 'python':
            return self._split_python(text)
        elif language in ['javascript', 'typescript', 'java', 'cpp', 'c', 'go', 'rust', 'php']:
            return self._split_brace_language(text)
        else:
            temp_doc = Document(text=text)
            nodes = self.fallback_splitter.get_nodes_from_documents([temp_doc])
            return [node.text for node in nodes]

    def _split_python(self, text: str) -> List[str]:
        """Split Python code by top-level definitions"""
        lines = text.split('\n')
        chunks = []
        current_chunk = []

        for line in lines:
            stripped = line.lstrip()

            if stripped.startswith(('def ', 'class ', 'async def ')):
                if current_chunk and len('\n'.join(current_chunk)) > 50:
                    chunks.append('\n'.join(current_chunk))
                    current_chunk = []

            current_chunk.append(line)

            if len('\n'.join(current_chunk)) > self.max_chunk_size:
                chunks.append('\n'.join(current_chunk))
                current_chunk = []

        if current_chunk:
            chunks.append('\n'.join(current_chunk))

        return chunks if chunks else [text]

    def _split_brace_language(self, text: str) -> List[str]:
        """Split brace-based languages by functions/classes"""
        chunks = []
        current_chunk = []
        brace_count = 0
        in_function = False

        lines = text.split('\n')

        for line in lines:
            if any(keyword in line for keyword in
                   ['function ', 'class ', 'def ', 'fn ', 'func ', 'public ', 'private ', 'protected ']):
                if '{' in line:
                    in_function = True

            current_chunk.append(line)

            brace_count += line.count('{') - line.count('}')

            if in_function and brace_count == 0 and len(current_chunk) > 3:
                chunks.append('\n'.join(current_chunk))
                current_chunk = []
                in_function = False

            if len('\n'.join(current_chunk)) > self.max_chunk_size:
                chunks.append('\n'.join(current_chunk))
                current_chunk = []
                brace_count = 0

        if current_chunk:
            chunks.append('\n'.join(current_chunk))

        return chunks if chunks else [text]

