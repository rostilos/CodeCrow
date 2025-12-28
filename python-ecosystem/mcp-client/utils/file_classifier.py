"""
File classification utility for prioritizing files in code review.
Implements Lost-in-the-Middle protection by categorizing files into priority sections.
"""
import os
import re
from typing import List, Dict, Tuple, Set
from dataclasses import dataclass, field
from enum import Enum


class FilePriority(str, Enum):
    """Priority levels for file analysis."""
    HIGH = "HIGH"      # Core business logic - analyze FIRST
    MEDIUM = "MEDIUM"  # Dependencies, shared utils
    LOW = "LOW"        # Tests, configs, generated files
    SKIP = "SKIP"      # Files to exclude from analysis


@dataclass
class ClassifiedFile:
    """Represents a classified file with its priority and metadata."""
    path: str
    priority: FilePriority
    category: str  # e.g., "core", "service", "test", "config"
    estimated_importance: float = 1.0  # 0.0 to 1.0
    reasons: List[str] = field(default_factory=list)


class FileClassifier:
    """
    Classifies files into priority categories for Lost-in-the-Middle protection.
    
    Priority Distribution (recommended):
    - HIGH: 30% of context budget - Core business logic
    - MEDIUM: 40% of context budget - Dependencies, shared utils
    - LOW: 20% of context budget - Tests, configs
    - RAG: 10% of context budget - Additional relevant files
    """
    
    # Patterns for HIGH priority files (core business logic)
    HIGH_PRIORITY_PATTERNS = [
        # Service/Business logic
        r'.*/service[s]?/.*\.(java|py|ts|js)$',
        r'.*/controller[s]?/.*\.(java|py|ts|js)$',
        r'.*/handler[s]?/.*\.(java|py|ts|js)$',
        r'.*/api/.*\.(java|py|ts|js)$',
        r'.*/core/.*\.(java|py|ts|js)$',
        r'.*/domain/.*\.(java|py|ts|js)$',
        r'.*/business/.*\.(java|py|ts|js)$',
        # Security-critical
        r'.*[Aa]uth.*\.(java|py|ts|js)$',
        r'.*[Ss]ecurity.*\.(java|py|ts|js)$',
        r'.*[Pp]ermission.*\.(java|py|ts|js)$',
        r'.*[Aa]ccess.*[Cc]ontrol.*\.(java|py|ts|js)$',
        # Database/Repository
        r'.*/repository/.*\.(java|py|ts|js)$',
        r'.*/dao/.*\.(java|py|ts|js)$',
        r'.*[Mm]igration.*\.(sql|py|java)$',
    ]
    
    # Patterns for MEDIUM priority files
    MEDIUM_PRIORITY_PATTERNS = [
        # Models/Entities
        r'.*/model[s]?/.*\.(java|py|ts|js)$',
        r'.*/entity/.*\.(java|py|ts|js)$',
        r'.*/dto/.*\.(java|py|ts|js)$',
        r'.*/schema/.*\.(java|py|ts|js)$',
        # Utils/Helpers
        r'.*/util[s]?/.*\.(java|py|ts|js)$',
        r'.*/helper[s]?/.*\.(java|py|ts|js)$',
        r'.*/common/.*\.(java|py|ts|js)$',
        r'.*/shared/.*\.(java|py|ts|js)$',
        # Components (Frontend)
        r'.*/components?/.*\.(tsx?|jsx?)$',
        r'.*/hooks?/.*\.(tsx?|jsx?)$',
        # Client/Integration
        r'.*/client[s]?/.*\.(java|py|ts|js)$',
        r'.*/integration/.*\.(java|py|ts|js)$',
    ]
    
    # Patterns for LOW priority files
    LOW_PRIORITY_PATTERNS = [
        # Tests
        r'.*[Tt]est.*\.(java|py|ts|js)$',
        r'.*[Ss]pec.*\.(ts|js)$',
        r'.*/test[s]?/.*',
        r'.*/__tests__/.*',
        # Configs
        r'.*\.(json|yaml|yml|toml|ini|cfg)$',
        r'.*[Cc]onfig.*\.(java|py|ts|js)$',
        r'.*/config/.*',
        # Documentation
        r'.*\.(md|txt|rst)$',
        r'.*/docs?/.*',
    ]
    
    # Patterns for files to SKIP entirely
    SKIP_PATTERNS = [
        # Generated files
        r'.*/dist/.*',
        r'.*/build/.*',
        r'.*/target/.*',
        r'.*/node_modules/.*',
        r'.*/__pycache__/.*',
        r'.*\.min\.(js|css)$',
        r'.*\.map$',
        r'.*\.d\.ts$',
        # Lock files
        r'.*lock.*\.(json|yaml)$',
        r'.*-lock\.(json|yaml)$',
        # IDE/Editor
        r'.*/\.idea/.*',
        r'.*/\.vscode/.*',
        # Assets
        r'.*\.(png|jpg|jpeg|gif|svg|ico|woff|ttf|eot)$',
    ]
    
    # Keywords that indicate high importance
    HIGH_IMPORTANCE_KEYWORDS = {
        'auth', 'security', 'login', 'password', 'token', 'session',
        'payment', 'transaction', 'billing', 'credit',
        'user', 'account', 'profile', 'permission', 'role',
        'api', 'endpoint', 'controller', 'handler',
        'database', 'repository', 'query', 'migration',
        'validation', 'sanitize', 'encrypt', 'decrypt',
    }
    
    @classmethod
    def classify_files(
        cls,
        file_paths: List[str],
        change_types: Dict[str, str] = None
    ) -> Dict[FilePriority, List[ClassifiedFile]]:
        """
        Classify a list of files into priority categories.
        
        Args:
            file_paths: List of file paths to classify
            change_types: Optional dict mapping file path to change type (added, modified, deleted)
            
        Returns:
            Dict mapping FilePriority to list of ClassifiedFile objects
        """
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
        
        # Sort within each priority by importance
        for priority in classified:
            classified[priority].sort(key=lambda f: f.estimated_importance, reverse=True)
        
        return classified
    
    @classmethod
    def _classify_single_file(cls, path: str, change_type: str = None) -> ClassifiedFile:
        """Classify a single file."""
        reasons = []
        
        # Check SKIP patterns first
        for pattern in cls.SKIP_PATTERNS:
            if re.match(pattern, path, re.IGNORECASE):
                return ClassifiedFile(
                    path=path,
                    priority=FilePriority.SKIP,
                    category="generated/ignored",
                    estimated_importance=0.0,
                    reasons=["Matches skip pattern"]
                )
        
        # Check HIGH priority patterns
        for pattern in cls.HIGH_PRIORITY_PATTERNS:
            if re.match(pattern, path, re.IGNORECASE):
                reasons.append(f"Matches high priority pattern")
                importance = cls._calculate_importance(path, change_type)
                return ClassifiedFile(
                    path=path,
                    priority=FilePriority.HIGH,
                    category=cls._get_category(path),
                    estimated_importance=importance,
                    reasons=reasons
                )
        
        # Check for high-importance keywords in path
        path_lower = path.lower()
        keyword_matches = [kw for kw in cls.HIGH_IMPORTANCE_KEYWORDS if kw in path_lower]
        if keyword_matches:
            reasons.append(f"Contains important keywords: {', '.join(keyword_matches)}")
            importance = cls._calculate_importance(path, change_type)
            return ClassifiedFile(
                path=path,
                priority=FilePriority.HIGH,
                category=cls._get_category(path),
                estimated_importance=importance,
                reasons=reasons
            )
        
        # Check MEDIUM priority patterns
        for pattern in cls.MEDIUM_PRIORITY_PATTERNS:
            if re.match(pattern, path, re.IGNORECASE):
                reasons.append("Matches medium priority pattern")
                importance = cls._calculate_importance(path, change_type) * 0.8
                return ClassifiedFile(
                    path=path,
                    priority=FilePriority.MEDIUM,
                    category=cls._get_category(path),
                    estimated_importance=importance,
                    reasons=reasons
                )
        
        # Check LOW priority patterns
        for pattern in cls.LOW_PRIORITY_PATTERNS:
            if re.match(pattern, path, re.IGNORECASE):
                reasons.append("Matches low priority pattern")
                importance = cls._calculate_importance(path, change_type) * 0.5
                return ClassifiedFile(
                    path=path,
                    priority=FilePriority.LOW,
                    category=cls._get_category(path),
                    estimated_importance=importance,
                    reasons=reasons
                )
        
        # Default to MEDIUM for unclassified files
        reasons.append("Default classification")
        importance = cls._calculate_importance(path, change_type) * 0.7
        return ClassifiedFile(
            path=path,
            priority=FilePriority.MEDIUM,
            category=cls._get_category(path),
            estimated_importance=importance,
            reasons=reasons
        )
    
    @classmethod
    def _calculate_importance(cls, path: str, change_type: str = None) -> float:
        """
        Calculate importance score based on file characteristics.
        
        Returns:
            Score from 0.0 to 1.0
        """
        score = 0.5  # Base score
        
        # Boost for new files (likely new features)
        if change_type == 'added':
            score += 0.2
        
        # Boost for files with security keywords
        path_lower = path.lower()
        security_keywords = {'auth', 'security', 'permission', 'password', 'token', 'encrypt'}
        if any(kw in path_lower for kw in security_keywords):
            score += 0.3
        
        # Boost for main entry points
        filename = os.path.basename(path).lower()
        if filename in {'main', 'index', 'app', 'application', 'server'}:
            score += 0.1
        
        return min(1.0, score)
    
    @classmethod
    def _get_category(cls, path: str) -> str:
        """Determine the category of a file based on its path."""
        path_lower = path.lower()
        
        if '/test' in path_lower or 'test' in os.path.basename(path_lower):
            return "test"
        if '/service' in path_lower:
            return "service"
        if '/controller' in path_lower or '/handler' in path_lower:
            return "controller"
        if '/repository' in path_lower or '/dao' in path_lower:
            return "repository"
        if '/model' in path_lower or '/entity' in path_lower or '/dto' in path_lower:
            return "model"
        if '/util' in path_lower or '/helper' in path_lower:
            return "utility"
        if '/config' in path_lower:
            return "config"
        if '/component' in path_lower:
            return "component"
        if '/api' in path_lower:
            return "api"
        
        # Determine by extension
        ext = os.path.splitext(path)[1].lower()
        if ext in {'.md', '.txt', '.rst'}:
            return "documentation"
        if ext in {'.json', '.yaml', '.yml', '.toml'}:
            return "config"
        
        return "other"
    
    @classmethod
    def get_priority_stats(cls, classified: Dict[FilePriority, List[ClassifiedFile]]) -> Dict[str, int]:
        """Get statistics about classified files."""
        return {
            "high": len(classified[FilePriority.HIGH]),
            "medium": len(classified[FilePriority.MEDIUM]),
            "low": len(classified[FilePriority.LOW]),
            "skipped": len(classified[FilePriority.SKIP]),
            "total": sum(len(files) for files in classified.values())
        }
    
    @classmethod
    def detect_core_files(cls, file_paths: List[str], max_core: int = 5) -> List[str]:
        """
        Detect and return the most critical core files from the list.
        Useful for quick identification of files that need most attention.
        """
        classified = cls.classify_files(file_paths)
        core_files = []
        
        # Take from HIGH priority first
        for f in classified[FilePriority.HIGH][:max_core]:
            core_files.append(f.path)
        
        # If not enough, add from MEDIUM
        remaining = max_core - len(core_files)
        if remaining > 0:
            for f in classified[FilePriority.MEDIUM][:remaining]:
                core_files.append(f.path)
        
        return core_files
