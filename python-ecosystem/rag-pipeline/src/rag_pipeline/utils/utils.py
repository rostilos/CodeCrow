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
    '.phtml': 'php',
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
    """Create a safe namespace identifier for indexing (includes branch)"""
    return f"{workspace}__{project}__{branch}".replace("/", "_").replace(".", "_").lower()


def make_project_namespace(workspace: str, project: str) -> str:
    """Create a safe namespace identifier for project-level collection (no branch)"""
    return f"{workspace}__{project}".replace("/", "_").replace(".", "_").lower()


def clean_archive_path(path: str) -> str:
    """
    Clean archive root prefix from file paths.
    
    Bitbucket and other VCS archives often create a root folder like:
    - 'owner-repo-commitHash/' (Bitbucket)
    - 'repo-branch/' (GitHub)
    
    This function strips that prefix to get clean paths like 'src/file.php'.
    
    Args:
        path: File path potentially with archive prefix
        
    Returns:
        Clean path without archive prefix
    """
    if not path:
        return path
    
    parts = Path(path).parts
    if len(parts) <= 1:
        return path
    
    first_part = parts[0]
    
    # Common source directory markers - if first part is one of these, path is already clean
    source_markers = {'src', 'lib', 'app', 'source', 'main', 'test', 'tests', 
                      'pkg', 'cmd', 'internal', 'bin', 'scripts', 'docs'}
    if first_part.lower() in source_markers:
        return path
    
    # Check if first part looks like archive root:
    # - Contains hyphens (owner-repo-commit pattern)
    # - Or is very long (40+ chars for commit hash)
    # - Or matches pattern like 'name-hexstring'
    looks_like_archive = (
        '-' in first_part and len(first_part) > 20 or  # owner-repo-commit
        len(first_part) >= 40 or  # Just commit hash
        (first_part.count('-') >= 2 and any(c.isdigit() for c in first_part))  # Has digits and multiple hyphens
    )
    
    if looks_like_archive:
        return '/'.join(parts[1:])
    
    return path


def should_exclude_file(path: str, excluded_patterns: list[str]) -> bool:
    """Check if file should be excluded based on patterns.
    
    Supports:
    - Exact directory matches: 'vendor/' matches 'vendor/file.php'
    - Single wildcard: 'vendor/*' matches 'vendor/file.php' but not 'vendor/sub/file.php'
    - Double wildcard (globstar): 'vendor/**' matches 'vendor/file.php' and 'vendor/sub/file.php'
    - File patterns: '*.min.js' matches any file ending with .min.js
    
    Note: Also handles paths with archive root prefix (e.g., 'repo-commit123/lib/file.php' 
    will match pattern 'lib/**')
    """
    from fnmatch import fnmatch
    
    path_obj = Path(path)
    path_str = str(path_obj)
    path_parts = path_obj.parts
    
    # Handle archive root prefix - if path starts with a single directory that looks like
    # an archive root (contains hyphen typically from bitbucket archives), try matching
    # against the path without that prefix as well
    paths_to_check = [path_str]
    if len(path_parts) > 1:
        # Add the path without the first directory component (archive root)
        path_without_root = '/'.join(path_parts[1:])
        paths_to_check.append(path_without_root)

    for check_path in paths_to_check:
        check_path_obj = Path(check_path)
        check_parts = check_path_obj.parts
        
        for pattern in excluded_patterns:
            # Handle ** (globstar) patterns
            if '**' in pattern:
                # Convert globstar pattern to check if path starts with the prefix
                # e.g., 'vendor/**' should match any path starting with 'vendor/'
                prefix = pattern.split('**')[0].rstrip('/')
                if prefix:
                    # Check if path starts with the prefix directory
                    if check_path.startswith(prefix + '/') or check_path == prefix:
                        return True
                    # Also check if any parent directory matches
                    for i in range(len(check_parts)):
                        partial_path = '/'.join(check_parts[:i+1])
                        if partial_path == prefix or partial_path.startswith(prefix + '/'):
                            return True
                else:
                    # Pattern like '**/*.min.js' - suffix matching
                    suffix = pattern.split('**')[-1].lstrip('/')
                    if suffix and fnmatch(check_path_obj.name, suffix):
                        return True
            else:
                # Standard fnmatch for non-globstar patterns
                if fnmatch(check_path, pattern):
                    return True
                if fnmatch(check_path_obj.name, pattern):
                    return True
                # Handle directory prefix patterns like 'vendor/' 
                if pattern.endswith('/'):
                    dir_prefix = pattern.rstrip('/')
                    if check_path.startswith(dir_prefix + '/'):
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

