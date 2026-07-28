from __future__ import annotations

import importlib
import threading
from dataclasses import dataclass
from typing import Iterator


_DOCUMENT_KEEPALIVE = threading.local()
_LANGUAGE_CACHE: dict[tuple[str, str], object] = {}
_LANGUAGE_CACHE_LOCK = threading.Lock()


def _cached_language(grammar_module: str, grammar_factory: str) -> object:
    key = (grammar_module, grammar_factory)
    with _LANGUAGE_CACHE_LOCK:
        language = _LANGUAGE_CACHE.get(key)
        if language is not None:
            return language
        from tree_sitter import Language

        module = importlib.import_module(grammar_module)
        factory = getattr(module, grammar_factory)
        language = Language(factory())
        _LANGUAGE_CACHE[key] = language
        return language


@dataclass(frozen=True)
class TreeSitterDocument:
    """Runtime-neutral source wrapper used by language/framework plugins.

    Concrete grammar package names remain plugin-owned. Imports are lazy so hosts
    that never execute syntax contributions do not need grammar dependencies.
    """

    source: bytes
    language: object
    tree: object

    @property
    def root(self) -> object:
        """Return the root while keeping its native owning Tree alive."""
        return self.tree.root_node

    @classmethod
    def parse(cls, content: str, grammar_module: str, grammar_factory: str) -> "TreeSitterDocument":
        from tree_sitter import Parser

        source = content.encode("utf-8")
        # Individual grammar wheels expose a shared native language capsule.
        # Repeatedly constructing and releasing wrappers around that capsule can
        # invalidate a subsequent parser in the same process, so cache one
        # wrapper per grammar for the worker lifetime.
        language = _cached_language(grammar_module, grammar_factory)
        tree = Parser(language).parse(source)
        # Native nodes borrow memory from both the parsed Tree and its grammar
        # Language. Keep both owners for the complete document lifetime. The
        # Python binding can release frame locals in an order where borrowed
        # Nodes are finalized after their document, so retain the most recent
        # document per worker thread until the next parse replaces it.
        document = cls(source=source, language=language, tree=tree)
        _DOCUMENT_KEEPALIVE.document = document
        return document

    def text(self, node: object | None) -> str:
        if node is None:
            return ""
        return self.source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    @staticmethod
    def line(node: object) -> int:
        return node.start_point.row + 1

    def walk(self, node: object | None = None) -> Iterator[object]:
        """Traverse named nodes with a native cursor.

        Repeated recursive materialization of ``Node.named_children`` is unsafe
        with some grammar/binding combinations (notably Java text blocks under
        tree-sitter 0.26) and can return corrupted borrowed nodes. A TreeCursor
        keeps traversal state owned by the Tree and does not allocate nested
        borrowed-child lists.
        """
        current = self.root if node is None else node
        cursor = current.walk()
        while True:
            candidate = cursor.node
            if candidate.is_named:
                yield candidate
            if cursor.goto_first_child():
                continue
            if cursor.goto_next_sibling():
                continue
            while cursor.goto_parent():
                if cursor.goto_next_sibling():
                    break
            else:
                return

    def descendants(self, node: object, *types: str) -> tuple[object, ...]:
        accepted = set(types)
        return tuple(candidate for candidate in self.walk(node) if candidate.type in accepted)
