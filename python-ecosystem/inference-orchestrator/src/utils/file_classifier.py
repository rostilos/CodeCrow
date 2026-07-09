"""
Compatibility file classification helpers.

The active review pipeline no longer performs deterministic semantic
prioritization from path names, extensions, or directory labels. This module is
kept for older imports/tests, but it intentionally returns neutral reviewable
classification and leaves semantic judgment to the LLM with structured context.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List


class FilePriority(str, Enum):
    """Compatibility priority levels for older callers."""
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    SKIP = "SKIP"


@dataclass
class ClassifiedFile:
    """Represents a file with neutral compatibility metadata."""
    path: str
    priority: FilePriority
    category: str
    estimated_importance: float = 1.0
    reasons: List[str] = field(default_factory=list)


class FileClassifier:
    """
    Neutral classifier retained for API compatibility.

    It does not infer business/security/test/config/generated priority from
    filenames or metadata. Callers that need semantic prioritization should pass
    structured metadata to the LLM planning stage.
    """

    @classmethod
    def classify_files(
        cls,
        file_paths: List[str],
        change_types: Dict[str, str] = None,
    ) -> Dict[FilePriority, List[ClassifiedFile]]:
        change_types = change_types or {}
        classified = {
            FilePriority.HIGH: [],
            FilePriority.MEDIUM: [],
            FilePriority.LOW: [],
            FilePriority.SKIP: [],
        }

        for path in file_paths:
            classified_file = cls._classify_single_file(path, change_types.get(path))
            classified[classified_file.priority].append(classified_file)

        return classified

    @classmethod
    def _classify_single_file(cls, path: str, change_type: str = None) -> ClassifiedFile:
        reasons = ["Neutral classification; semantic priority is decided by the LLM"]
        if change_type:
            reasons.append(f"Change type: {change_type}")

        return ClassifiedFile(
            path=path,
            priority=FilePriority.MEDIUM,
            category="reviewable",
            estimated_importance=cls._calculate_importance(path, change_type),
            reasons=reasons,
        )

    @classmethod
    def _calculate_importance(cls, path: str, change_type: str = None) -> float:
        if change_type == "deleted":
            return 0.0
        return 1.0

    @classmethod
    def _get_category(cls, path: str) -> str:
        return "reviewable"

    @classmethod
    def get_priority_stats(cls, classified: Dict[FilePriority, List[ClassifiedFile]]) -> Dict[str, int]:
        return {
            "high": len(classified[FilePriority.HIGH]),
            "medium": len(classified[FilePriority.MEDIUM]),
            "low": len(classified[FilePriority.LOW]),
            "skipped": len(classified[FilePriority.SKIP]),
            "total": sum(len(files) for files in classified.values()),
        }

    @classmethod
    def detect_core_files(cls, file_paths: List[str], max_core: int = 5) -> List[str]:
        return list(file_paths[:max_core])
