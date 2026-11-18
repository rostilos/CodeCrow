"""Repository images are outside analysis scope, independently of plugin support."""
from pathlib import Path
import re
import shlex

IMAGE_EXTENSIONS = frozenset(line for line in Path(__file__).with_name('image_extensions.txt').read_text().splitlines()
                             if line and not line.startswith('#'))


def is_image_file(path):
    if not isinstance(path, str):
        return False
    name = path.replace('\\', '/').rsplit('/', 1)[-1].casefold()
    suffixes = name.split('.')
    if suffixes[-1] in {'gz', 'bz2', 'xz', 'zst', 'br'}:
        suffixes = suffixes[:-1]
    return len(suffixes) > 1 and suffixes[-1] in IMAGE_EXTENSIONS


def filter_image_diff(diff):
    if not isinstance(diff, str):
        return diff
    parts = re.split(r'(?=^diff --git )', diff, flags=re.MULTILINE)
    kept = []
    for part in parts:
        if part.startswith('diff --git '):
            try:
                paths = shlex.split(part.splitlines()[0])[2:]
            except ValueError:
                paths = part.splitlines()[0].split(' b/', 1)
            if any(is_image_file(path.strip('"')) for path in paths):
                continue
        kept.append(part)
    return ''.join(kept)


_PATH_FIELDS = {'path', 'filePath', 'file_path', 'file', 'source', 'target', 'primary_file'}
_PATH_LISTS = {'paths', 'changedFiles', 'deletedFiles', 'relatedPaths', 'affected_files',
               'queriedPaths', 'unrepresentedPaths', 'nonStructuralPaths', 'reviewFilePaths',
               'fullPrFilePaths', 'changed_files', 'deleted_files', 'targetFiles', 'scanBatches'}


def omit_image_records(value, field=''):
    """Remove image-owned records from stored/input context without altering source text."""
    if isinstance(value, dict):
        if any(is_image_file(value.get(key)) for key in _PATH_FIELDS):
            return None
        nodes = value.get('nodes')
        removed_nodes = {node.get('id') for node in (nodes if isinstance(nodes, list) else [])
                         if isinstance(node, dict) and isinstance(node.get('id'), (str, int))
                         and is_image_file(node.get('path'))}
        result = {}
        for key, item in value.items():
            if is_image_file(key):
                continue
            if key == 'edges' and removed_nodes and isinstance(item, list):
                item = [edge for edge in item if not isinstance(edge, dict)
                        or not any(edge.get(end) in removed_nodes for end in ('source', 'target'))]
            filtered = omit_image_records(item, key)
            if filtered is not None or item is None:
                result[key] = filtered
        if isinstance(value.get('paths'), list) and value['paths'] and not result.get('paths'):
            return None
        return value if result == value else result
    if isinstance(value, list):
        result = []
        for item in value:
            if field in _PATH_LISTS and is_image_file(item):
                continue
            filtered = omit_image_records(item, field)
            if filtered is not None or item is None:
                result.append(filtered)
        return value if result == value else result
    if field in {'rawDiff', 'deltaDiff', 'diff'}:
        return filter_image_diff(value)
    if field in {'oldPath', 'old_path', 'newPath'} and is_image_file(value):
        return None
    return value
