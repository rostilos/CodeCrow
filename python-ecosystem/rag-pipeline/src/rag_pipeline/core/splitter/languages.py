"""
Language detection and mapping for AST-based code splitting.

Maps file extensions to tree-sitter language names and LangChain Language enum.
"""

from pathlib import Path
from typing import Dict, Optional, Set
from langchain_text_splitters import Language


# Map file extensions to LangChain Language enum (for RecursiveCharacterTextSplitter fallback)
EXTENSION_TO_LANGUAGE: Dict[str, Language] = {
    # Python
    '.py': Language.PYTHON,
    '.pyw': Language.PYTHON,
    '.pyi': Language.PYTHON,

    # Java/JVM
    '.java': Language.JAVA,
    '.kt': Language.KOTLIN,
    '.kts': Language.KOTLIN,
    '.scala': Language.SCALA,

    # JavaScript/TypeScript
    '.js': Language.JS,
    '.jsx': Language.JS,
    '.mjs': Language.JS,
    '.cjs': Language.JS,
    '.ts': Language.TS,
    '.tsx': Language.TS,

    # Systems languages
    '.go': Language.GO,
    '.rs': Language.RUST,
    '.c': Language.C,
    '.h': Language.C,
    '.cpp': Language.CPP,
    '.cc': Language.CPP,
    '.cxx': Language.CPP,
    '.hpp': Language.CPP,
    '.hxx': Language.CPP,
    '.cs': Language.CSHARP,

    # Web/Scripting
    '.php': Language.PHP,
    '.phtml': Language.PHP,
    '.php3': Language.PHP,
    '.php4': Language.PHP,
    '.php5': Language.PHP,
    '.phps': Language.PHP,
    '.inc': Language.PHP,
    '.rb': Language.RUBY,
    '.erb': Language.RUBY,
    '.lua': Language.LUA,
    '.pl': Language.PERL,
    '.pm': Language.PERL,
    '.swift': Language.SWIFT,

    # Markup/Config
    '.md': Language.MARKDOWN,
    '.markdown': Language.MARKDOWN,
    '.html': Language.HTML,
    '.htm': Language.HTML,
    '.rst': Language.RST,
    '.tex': Language.LATEX,
    '.proto': Language.PROTO,
    '.sol': Language.SOL,
    '.hs': Language.HASKELL,
    '.cob': Language.COBOL,
    '.cbl': Language.COBOL,
    '.xml': Language.HTML,
}

# Languages that support full AST parsing via tree-sitter
AST_SUPPORTED_LANGUAGES: Set[Language] = {
    Language.PYTHON, Language.JAVA, Language.KOTLIN, Language.JS, Language.TS,
    Language.GO, Language.RUST, Language.C, Language.CPP, Language.CSHARP,
    Language.PHP, Language.RUBY, Language.SCALA, Language.LUA, Language.PERL,
    Language.SWIFT, Language.HASKELL, Language.COBOL
}

# Map LangChain Language enum to tree-sitter language name
LANGUAGE_TO_TREESITTER: Dict[Language, str] = {
    Language.PYTHON: 'python',
    Language.JAVA: 'java',
    Language.KOTLIN: 'kotlin',
    Language.JS: 'javascript',
    Language.TS: 'typescript',
    Language.GO: 'go',
    Language.RUST: 'rust',
    Language.C: 'c',
    Language.CPP: 'cpp',
    Language.CSHARP: 'c_sharp',
    Language.PHP: 'php',
    Language.RUBY: 'ruby',
    Language.SCALA: 'scala',
    Language.LUA: 'lua',
    Language.PERL: 'perl',
    Language.SWIFT: 'swift',
    Language.HASKELL: 'haskell',
}

# Map tree-sitter language name to module info: (module_name, function_name)
TREESITTER_MODULES: Dict[str, tuple] = {
    'python': ('tree_sitter_python', 'language'),
    'java': ('tree_sitter_java', 'language'),
    'javascript': ('tree_sitter_javascript', 'language'),
    'typescript': ('tree_sitter_typescript', 'language_typescript'),
    'go': ('tree_sitter_go', 'language'),
    'rust': ('tree_sitter_rust', 'language'),
    'c': ('tree_sitter_c', 'language'),
    'cpp': ('tree_sitter_cpp', 'language'),
    'c_sharp': ('tree_sitter_c_sharp', 'language'),
    'ruby': ('tree_sitter_ruby', 'language'),
    'php': ('tree_sitter_php', 'language_php'),
}


def get_language_from_path(path: str) -> Optional[Language]:
    """Determine LangChain Language enum from file path."""
    ext = Path(path).suffix.lower()
    return EXTENSION_TO_LANGUAGE.get(ext)


def get_treesitter_name(language: Language) -> Optional[str]:
    """Get tree-sitter language name from LangChain Language enum."""
    return LANGUAGE_TO_TREESITTER.get(language)


def is_ast_supported(path: str) -> bool:
    """Check if AST parsing is supported for a file."""
    language = get_language_from_path(path)
    return language is not None and language in AST_SUPPORTED_LANGUAGES


def get_supported_languages() -> list:
    """Return list of languages with AST support."""
    return list(LANGUAGE_TO_TREESITTER.values())
