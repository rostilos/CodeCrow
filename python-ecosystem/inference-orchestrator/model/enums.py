from enum import Enum


class IssueCategory(str, Enum):
    """Valid issue categories for code analysis."""
    SECURITY = "SECURITY"
    PERFORMANCE = "PERFORMANCE"
    CODE_QUALITY = "CODE_QUALITY"
    BUG_RISK = "BUG_RISK"
    STYLE = "STYLE"
    DOCUMENTATION = "DOCUMENTATION"
    BEST_PRACTICES = "BEST_PRACTICES"
    ERROR_HANDLING = "ERROR_HANDLING"
    TESTING = "TESTING"
    ARCHITECTURE = "ARCHITECTURE"


class AnalysisMode(str, Enum):
    """Analysis mode for PR reviews."""
    FULL = "FULL"  # Full PR diff analysis (first review or escalation)
    INCREMENTAL = "INCREMENTAL"  # Delta diff analysis (subsequent reviews)


class RelationshipType(str, Enum):
    """Types of relationships between files."""
    IMPORTS = "IMPORTS"
    EXTENDS = "EXTENDS"
    IMPLEMENTS = "IMPLEMENTS"
    CALLS = "CALLS"
    SAME_PACKAGE = "SAME_PACKAGE"
    REFERENCES = "REFERENCES"
