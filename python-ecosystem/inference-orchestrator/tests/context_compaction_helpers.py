"""Independent expansion used to prove prompt compaction preserves facts."""
import json


def expand_context(value):
    if isinstance(value, str):
        value = json.loads(value)
    if 'referenceKey' in value:
        key, values = value['referenceKey'], value['values']

        def expand(item):
            if isinstance(item, dict):
                if set(item) == {key}:
                    return expand(values[item[key]])
                return {name: expand(child) for name, child in item.items()}
            if isinstance(item, list):
                return [expand(child) for child in item]
            return item

        value = expand(value['context'])
    graph = value.get('graph', value)
    if isinstance(graph, dict) and graph.get('edgeColumns', [])[2:3] == ['relationKindIndices']:
        graph['edges'] = [[row[0], row[1], kind, *row[3:]] for row in graph['edges'] for kind in row[2]]
        graph['edgeColumns'][2] = 'relationKindIndex'
        graph['encoding'] = graph['encoding'].removesuffix(
            ' Each relationKindIndices entry expands to a separate edge with the same endpoints, counts and origins.')
    return value


def canonical_context(context):
    value = json.loads(json.dumps(context))
    graph = value.get('graph', value)
    if isinstance(graph, dict) and 'entities' in graph and 'edges' in graph:
        graph['edges'].sort(key=lambda row: json.dumps(row, sort_keys=True))
    return value
