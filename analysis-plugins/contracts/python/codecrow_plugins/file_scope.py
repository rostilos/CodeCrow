"""Repository file scope shared by acquisition, request and persistence boundaries."""
from pathlib import Path
import re

EXCLUDED_EXTENSIONS = frozenset(line for line in Path(__file__).with_name('excluded_file_extensions.txt').read_text().splitlines()
                             if line and not line.startswith('#'))


def is_excluded(path):
    if not isinstance(path, str):
        return False
    name = path.replace('\\', '/').rsplit('/', 1)[-1].casefold()
    suffixes = name.split('.')
    if suffixes[-1] in {'gz', 'bz2', 'xz', 'zst', 'br'}:
        suffixes = suffixes[:-1]
    return len(suffixes) > 1 and suffixes[-1] in EXCLUDED_EXTENSIONS


def filter_diff(diff):
    if not isinstance(diff, str):
        return diff
    parts = re.split(r'(?=^diff --git )', diff, flags=re.MULTILINE)
    kept = []
    for part in parts:
        if part.startswith('diff --git '):
            header = re.match(r'^diff --git "?a/(.*?)"? "?b/(.*?)"?$', part.splitlines()[0])
            if header and any(is_excluded(path) for path in header.groups()):
                continue
        kept.append(part)
    return ''.join(kept)


_PATH_FIELDS = {'path', 'filePath', 'file_path', 'file', 'source', 'target', 'primary_file', 'sourceFile', 'targetFile', 'filename'}
_PATH_LISTS = {'paths', 'changedFiles', 'deletedFiles', 'relatedPaths', 'affected_files',
               'queriedPaths', 'unrepresentedPaths', 'nonStructuralPaths', 'reviewFilePaths',
               'fullPrFilePaths', 'changed_files', 'deleted_files', 'targetFiles', 'scanBatches'}


def filter_records(value, field=''):
    """Apply file scope to structured context without altering source text."""
    if isinstance(value, dict):
        if any(is_excluded(value.get(key)) for key in _PATH_FIELDS):
            return None
        if any(isinstance(value.get(key), dict) and is_excluded(value[key].get('path'))
               for key in ('origin', 'sourceUnit', 'targetUnit')):
            return None
        if isinstance(value.get('relatedPaths'), list) and any(map(is_excluded, value['relatedPaths'])):
            return None
        nodes = value.get('nodes')
        removed_nodes = {node.get('id') for node in (nodes if isinstance(nodes, list) else [])
                         if isinstance(node, dict) and isinstance(node.get('id'), (str, int))
                         and is_excluded(node.get('path'))}
        result = {}
        for key, item in value.items():
            if is_excluded(key):
                continue
            if key == 'edges' and removed_nodes and isinstance(item, list):
                item = [edge for edge in item if not isinstance(edge, dict)
                        or not any(isinstance(edge.get(end), (str, int)) and edge[end] in removed_nodes
                                   for end in ('source', 'target'))]
            filtered = filter_records(item, key)
            if filtered is not None or item is None:
                result[key] = filtered
        if isinstance(value.get('paths'), list) and value['paths'] and not result.get('paths'):
            return None
        return value if result == value else result
    if isinstance(value, list):
        result = []
        for item in value:
            if field in _PATH_LISTS and is_excluded(item):
                continue
            filtered = filter_records(item, field)
            if filtered is not None or item is None:
                result.append(filtered)
        return value if result == value else result
    if field in {'rawDiff', 'deltaDiff', 'diff'}:
        return filter_diff(value)
    if field in {'oldPath', 'old_path', 'newPath'} and is_excluded(value):
        return None
    return value
