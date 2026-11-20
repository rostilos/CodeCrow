"""Movable PR context records for graph-connected review packets."""
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class ChangeContextRecord:
    key: str
    field: str
    value: Any
    paths: tuple[str, ...]


def _paths(values):
    return tuple(dict.fromkeys(path for path in (values or ()) if isinstance(path, str) and path))


def _entity_path(graph, index):
    if index is None:
        return None
    kind, value = graph['entities'][index]
    return graph['paths'][value] if kind != 'unresolved_symbol' and value is not None else None


def _symbol_paths(symbols, indices):
    return _paths(symbols['paths'][value] for index in indices
                  for value in (symbols['nodes'][index][0], symbols['nodes'][index][5])
                  if value is not None)


def change_context_records(context: Mapping[str, Any]) -> list[ChangeContextRecord]:
    records = []

    def add(field, value, paths):
        records.append(ChangeContextRecord(f'{field}:{len(records)}', field, value, _paths(paths)))

    for node in context.get('files', []):
        add('files', node, [node.get('path')])
    for area in context.get('changeAreas', []):
        add('changeAreas', area, area.get('paths', []))
    for edge in context.get('relationships', []):
        kind = (edge.get('targetReference') or {}).get('kind')
        add('relationships', edge, [edge.get('source'), edge.get('target') if kind in (None, 'file') else None])

    graph = context.get('graph')
    if not isinstance(graph, dict):
        return records
    if 'entities' not in graph:
        add('graph', graph, [])
        return records
    for index, edge in enumerate(graph.get('edges', [])):
        add('graph_edges', index, [_entity_path(graph, edge[0]), _entity_path(graph, edge[1]),
                                  *(_entity_path(graph, origin[0]) for origin in edge[4] if origin[0] is not None)]
            if len(edge) > 4 else [_entity_path(graph, edge[0]), _entity_path(graph, edge[1])])
    symbols = graph.get('symbolDetail') or {}
    for index in range(len(symbols.get('nodes', []))):
        add('symbol_nodes', index, _symbol_paths(symbols, [index]))
    for index, edge in enumerate(symbols.get('edges', [])):
        add('symbol_edges', index, [*_symbol_paths(symbols, {node for observation in edge[2] for node in observation[1:3]}),
            *(symbols['paths'][observation[3]] for observation in edge[2] if observation[3] is not None)])
    return records


def _table(values, indices):
    return {str(index): values[index] for index in sorted(set(indices) - {None})}


def _coverage(coverage, paths):
    result = dict(coverage)
    for key in ('queriedPaths', 'unrepresentedPaths', 'nonStructuralPaths'):
        values = result.get(key)
        if isinstance(values, list):
            result[key + 'Count'] = len(values)
            result[key] = [value for value in values
                           if (value.get('path') if isinstance(value, dict) else value) in paths]
    result['scope'] = 'Global coverage status/counts; path lists describe only this packet. Absence is not proof of coverage.'
    return result


def _graph_slice(graph, records):
    if 'entities' not in graph:
        return graph
    paths = {path for record in records for path in record.paths}
    edges = [graph['edges'][record.value] for record in records if record.field == 'graph_edges']
    entity_ids = {index for edge in edges for index in edge[:2]}
    entity_ids.update(origin[0] for edge in edges if len(edge) > 4 for origin in edge[4] if origin[0] is not None)
    path_ids = {graph['entities'][index][1] for index in entity_ids
                if graph['entities'][index][0] != 'unresolved_symbol' and graph['entities'][index][1] is not None}
    result = {key: value for key, value in graph.items()
              if key not in {'paths', 'entities', 'edges', 'relationKinds', 'symbolDetail', 'coverage'}}
    result.update(paths=_table(graph['paths'], path_ids), entities=_table(graph['entities'], entity_ids),
                  relationKinds=_table(graph['relationKinds'], [edge[2] for edge in edges]), edges=edges,
                  coverage=_coverage(graph.get('coverage', {}), paths))
    result['encoding'] = graph.get('encoding', '').replace('Full file graph.', 'Assigned file graph records.') + (
        ' Numeric references use keys in the supplied lookup tables; keys need not be contiguous. '
        'Only assigned records are present; absent relationships may be in other packets.'
    )

    symbols = graph.get('symbolDetail')
    if isinstance(symbols, dict):
        symbol_edges = [symbols['edges'][record.value] for record in records if record.field == 'symbol_edges']
        node_ids = {record.value for record in records if record.field == 'symbol_nodes'}
        node_ids.update(node for edge in symbol_edges for observation in edge[2] for node in observation[1:3])
        symbol_paths = {value for index in node_ids for value in (symbols['nodes'][index][0], symbols['nodes'][index][5])
                        if value is not None}
        symbol_paths.update(observation[3] for edge in symbol_edges for observation in edge[2] if observation[3] is not None)
        selected = {key: value for key, value in symbols.items() if key not in {
            'paths', 'nodes', 'edges', 'nodeKinds', 'relationKinds', 'extractors', 'coverage'}}
        selected.update(paths=_table(symbols.get('paths', []), symbol_paths),
            nodes=_table(symbols.get('nodes', []), node_ids), edges=symbol_edges,
            nodeKinds=_table(symbols.get('nodeKinds', []), [symbols['nodes'][index][2] for index in node_ids]),
            relationKinds=_table(symbols.get('relationKinds', []), [edge[0] for edge in symbol_edges]),
            extractors=_table(symbols.get('extractors', []), [observation[5] for edge in symbol_edges for observation in edge[2]]),
            coverage=_coverage(symbols.get('coverage', {}), paths))
        selected['encoding'] = symbols.get('encoding', '') + ' Numeric references use lookup-table keys, not contiguous array positions.'
        result['symbolDetail'] = selected
    return result


def scoped_change_context(context: Mapping[str, Any], records: Sequence[ChangeContextRecord]) -> dict:
    """Render assigned records; the saved context and other packets keep the rest."""
    if not context:
        return {}
    result = {key: value for key, value in context.items() if key not in {'files', 'changeAreas', 'relationships', 'graph'}}
    result['contextScope'] = ('This packet contains assigned PR change records and a shared overview. '
        'The complete saved record is distributed across packets; absence here does not establish absence from the PR.')
    for field in ('files', 'changeAreas', 'relationships'):
        result[field] = [record.value for record in records if record.field == field]
    if isinstance(context.get('graph'), dict):
        result['graph'] = _graph_slice(context['graph'], records)
    return result


def focused_change_context(context, focus_paths, records, symbol_detail=None):
    """Local review gets its files and structural neighbors; Stage 2 owns the full record set."""
    focus = set(focus_paths)
    adjacent = [record for record in records if record.field == 'graph_edges' and focus.intersection(record.paths)]
    neighbors = focus | {path for record in adjacent for path in record.paths}
    selected = []
    for record in records:
        paths = set(record.paths)
        if record.field in {'graph_edges', 'symbol_edges'}:
            include = bool(focus & paths)
        elif record.field in {'relationships', 'changeAreas'}:
            include = bool(paths) and paths.issubset(neighbors)
        else:
            include = bool(neighbors & paths)
        if include:
            selected.append(record)
    result = scoped_change_context(context, selected)
    if symbol_detail is not None and isinstance(result.get('graph'), dict):
        result['graph']['symbolDetail'] = {**symbol_detail, 'coverage': _coverage(symbol_detail.get('coverage', {}), neighbors)}
    return result
