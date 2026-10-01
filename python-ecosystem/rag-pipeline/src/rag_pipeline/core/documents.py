"""Small internal document records used by the structural index."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Document:
    """Repository source plus its tenant/revision metadata."""

    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    id_: str | None = None


@dataclass
class TextNode(Document):
    """One independently persisted source or structural record."""

