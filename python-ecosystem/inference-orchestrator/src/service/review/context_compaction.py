"""Lossless prompt representation of change descriptions and file relationships."""
from collections import Counter
from copy import deepcopy
import json


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def compact_change_context(context: dict) -> str:
    """Share repeated values and group equivalent edge endpoints; omit no facts."""
    original = _json(context)
    compact = deepcopy(context)
    graph = compact.get('graph', compact)
    if (isinstance(graph, dict) and graph.get('edgeColumns', [None] * 3)[2:3] == ['relationKindIndex']
            and 'entities' in graph):
        groups = {}
        for edge in graph['edges']:
            key = _json([edge[0], edge[1], *edge[3:]])
            groups.setdefault(key, []).append(edge[2])
        graph['edges'] = [[row[0], row[1], kinds, *row[2:]]
                          for key, kinds in groups.items() for row in [json.loads(key)]]
        graph['edgeColumns'][2] = 'relationKindIndices'
        graph['encoding'] = graph.get('encoding', '') + (
            ' Each relationKindIndices entry expands to a separate edge with the same endpoints, counts and origins.'
        )

    counts, keys = Counter(), set()

    def collect(value):
        if isinstance(value, (str, dict, list)):
            counts[_json(value)] += 1
        if isinstance(value, dict):
            keys.update(value)
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(compact)
    reference_key = '$ref'
    while reference_key in keys:
        reference_key += '_'
    # Pool only when the exact repeated value costs more than its references/table entry.
    reference_bytes = len(_json({reference_key: len(counts)}).encode())
    repeated = {value for value, count in counts.items() if count > 1
                and (len(value.encode()) - reference_bytes) * (count - 1) > reference_bytes + 1}
    values, indices = [], {}

    def encode(value, *, reference=True):
        signature = _json(value) if isinstance(value, (str, dict, list)) else None
        if reference and signature in repeated:
            if signature not in indices:
                indices[signature] = len(values)
                values.append(None)
                values[indices[signature]] = encode(value, reference=False)
            return {reference_key: indices[signature]}
        if isinstance(value, dict):
            return {key: encode(child) for key, child in value.items()}
        if isinstance(value, list):
            return [encode(child) for child in value]
        return value

    encoded = encode(compact)
    if values:
        compact = dict(
            encoding=f'Every {{{reference_key}:N}} means the exact JSON value values[N]; expand nested references too. '
                     'Expand path references when calling tools. No descriptions or relationships are omitted.',
            referenceKey=reference_key, values=values, context=encoded,
        )
    rendered = _json(compact)
    return rendered if len(rendered.encode()) < len(original.encode()) else original
