"""Immutable coordinates and receipts for exact repository context."""

from hashlib import sha256
import json
import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


EXACT_REVISION_PATTERN = r"^[0-9a-fA-F]{40,64}$"
DEFAULT_PARSER_VERSION = os.environ.get("RAG_PARSER_VERSION", "tree-sitter-v1")
DEFAULT_CHUNKER_VERSION = os.environ.get("RAG_CHUNKER_VERSION", "ast-code-splitter-v1")
DEFAULT_EMBEDDING_VERSION = os.environ.get("RAG_EMBEDDING_VERSION", "configured-v1")


class ContextSnapshotV1(BaseModel):
    """Content and processing identity for one pull-request snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    base_sha: str = Field(pattern=EXACT_REVISION_PATTERN)
    head_sha: str = Field(pattern=EXACT_REVISION_PATTERN)
    merge_base_sha: str = Field(pattern=EXACT_REVISION_PATTERN)
    parser_version: str = Field(default=DEFAULT_PARSER_VERSION, min_length=1, max_length=128)
    chunker_version: str = Field(default=DEFAULT_CHUNKER_VERSION, min_length=1, max_length=128)
    embedding_version: str = Field(default=DEFAULT_EMBEDDING_VERSION, min_length=1, max_length=128)

    @property
    def identity(self) -> str:
        encoded = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return sha256(encoded).hexdigest()
