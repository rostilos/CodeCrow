from pathlib import Path
from typing import Dict


LANGUAGE_MAP: Dict[str, str] = {
    '.py': 'python',
    '.js': 'javascript',
    '.jsx': 'javascript',
    '.ts': 'typescript',
    '.tsx': 'typescript',
    '.java': 'java',
    '.kt': 'kotlin',
    '.php': 'php',
    '.go': 'go',
    '.rs': 'rust',
    '.cpp': 'cpp',
    '.cc': 'cpp',
    '.cxx': 'cpp',
    '.c': 'c',
    '.h': 'c',
    '.hpp': 'cpp',
    '.rb': 'ruby',
    '.cs': 'csharp',
    '.swift': 'swift',
    '.m': 'objective-c',
    '.scala': 'scala',
    '.sh': 'bash',
    '.bash': 'bash',
    '.zsh': 'zsh',
    '.sql': 'sql',
    '.r': 'r',
    '.R': 'r',
    '.lua': 'lua',
    '.pl': 'perl',
    '.md': 'markdown',
    '.rst': 'rst',
    '.txt': 'text',
    '.json': 'json',
    '.xml': 'xml',
    '.yaml': 'yaml',
    '.yml': 'yaml',
    '.toml': 'toml',
    '.ini': 'ini',
    '.conf': 'config',
    '.html': 'html',
    '.htm': 'html',
    '.css': 'css',
    '.scss': 'scss',
    '.sass': 'sass',
    '.vue': 'vue',
    '.svelte': 'svelte',
}


def detect_language_from_path(path: str) -> str:
    """Detect programming language from file extension"""
    ext = Path(path).suffix.lower()
    return LANGUAGE_MAP.get(ext, 'text')


def make_namespace(workspace: str, project: str, branch: str) -> str:
    """Create a safe namespace identifier for indexing"""
    return f"{workspace}__{project}__{branch}".replace("/", "_").replace(".", "_").lower()


def should_exclude_file(path: str, excluded_patterns: list[str]) -> bool:
    """Check if file should be excluded based on patterns"""
    from fnmatch import fnmatch

    path_obj = Path(path)

    for pattern in excluded_patterns:
        if fnmatch(str(path_obj), pattern):
            return True
        if fnmatch(path_obj.name, pattern):
            return True

    return False


def is_binary_file(file_path: Path) -> bool:
    """Check if file is binary"""
    try:
        with open(file_path, 'rb') as f:
            chunk = f.read(1024)
            if b'\0' in chunk:
                return True
        return False
    except Exception:
        return True


def is_code_file(language: str) -> bool:
    """Check if the language represents code (vs text/config)"""
    non_code = {'text', 'markdown', 'rst', 'json', 'xml', 'yaml', 'toml', 'ini', 'config'}
    return language not in non_code

