"""Revision-bound dependency evidence for the cumulative PR change scan.

Reads existing graph generations; it never builds indexes or asks a model to
invent structural facts. Query failures remain visible optional enrichment.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging

from service.review.snapshot_identity import resolve_exact_structural_base_revision
from service.review.execution_scheduler import review_context_slot

logger = logging.getLogger(__name__)


def _identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]


def _overlaps(start, end, ranges):
    if not isinstance(start, int):
        return False
    end = end if isinstance(end, int) else start
    return any(start <= first + max(count, 1) - 1 and end >= first for first, count in ranges)


def _symbol_graph_context(graph):
    """Lossless relationship/location table without repeated JSON keys or hashes.

The persisted graph keeps native unit/evidence IDs and query receipts. Prompts
carry every endpoint, edge, snapshot and origin location; numbered paths/nodes
are local references, not graph-store identities. Model tools can resolve the
supplied path and symbol when deeper source inspection is needed.
"""
    paths = sorted({n['path'] for n in graph['nodes'] if n.get('path')} |
                   {o['origin']['path'] for e in graph['edges'] for o in e['observations']
                    if o.get('origin', {}).get('path')} |
                   {n['origin']['path'] for n in graph['nodes'] if (n.get('origin') or {}).get('path')})
    path_ids = {path: i for i, path in enumerate(paths)}
    node_kinds, relation_kinds, extractors = [], [], []
    def intern(table, value):
        if value not in table:
            table.append(value)
        return table.index(value)
    def merge_snapshot(values, value):
        for old in values:
            if old[1:] == value[1:]:
                old[0] |= value[0]
                return
        values.append(value)
    nodes, ids, semantic_nodes = [], {}, {}
    for node in graph['nodes']:
        name = node.get('qualifiedName') or node.get('name') or ''
        name = name.replace(f":{node.get('startLine')}-{node.get('endLine')}:", ':')
        if node.get('path') and name.startswith(node['path'] + ':'):
            name = name[len(node['path']) + 1:]
        identity = (node.get('path'), name, node.get('kind'), node['resolution'],
                    (node.get('origin') or {}).get('path'))
        if identity not in semantic_nodes:
            semantic_nodes[identity] = len(nodes)
            nodes.append([path_ids.get(node.get('path')), name, intern(node_kinds, node.get('kind')),
                          {'indexed': 0, 'unresolved': 1, 'file_only': 2}[node['resolution']], [],
                          path_ids.get((node.get('origin') or {}).get('path'))])
        index = semantic_nodes[identity]
        mask = 1 if node['snapshot'] == 'base' else 2
        # Unresolved source locations are carried on each edge observation.
        location = [mask, node.get('startLine'), node.get('endLine')]
        merge_snapshot(nodes[index][4], location)
        ids[node['id']] = index
    edges = []
    changes = ['retained', 'added', 'removed', 'unknown']
    for edge in graph['edges']:
        observations = []
        for observation in edge['observations']:
            origin = observation.get('origin') or {}
            value = [1 if observation['snapshot'] == 'base' else 2,
                     ids[observation['source']], ids[observation['target']],
                     path_ids.get(origin.get('path')), origin.get('line'),
                     intern(extractors, origin.get('extractor'))]
            if observation.get('attributes'):
                value.append(observation['attributes'])
            merge_snapshot(observations, value)
        edges.append([intern(relation_kinds, [edge['kind'], edge['relationship']]),
                      changes.index(edge['change']), observations])
    return dict(scope=graph['scope'], snapshots=graph['snapshots'], paths=paths,
        encoding='Array indices reference the named tables. Snapshot masks: 1=base, 2=proposed, 3=both. Node resolution: 0=indexed declaration, 1=unresolved, 2=plugin-resolved file only (symbol not verified). Optional attributes default to empty.',
        nodeKinds=node_kinds, relationKinds=relation_kinds, changes=changes, extractors=extractors,
        nodeColumns=['pathIndex', 'symbol', 'kindIndex', 'resolution', 'locations', 'originPathIndex'],
        locationColumns=['snapshotMask', 'startLine', 'endLine'], nodes=nodes,
        edgeColumns=['relationKindIndex', 'changeIndex', 'observations'],
        observationColumns=['snapshotMask', 'sourceNodeIndex', 'targetNodeIndex', 'originPathIndex', 'line', 'extractorIndex', 'attributes?'],
        edges=edges, coverage=graph['coverage'])


def graph_prompt_context(graph, focus_paths=()):
    """Complete file dependency map plus exact symbol detail for the focus.

Aggregation retains every collected relation, its kind, revision and origin
lines. Unresolved symbols stay distinct from real repository paths. Full unit
IDs and individual evidence receipts remain in the persisted graph.
"""
    by_id = {node['id']: node for node in graph['nodes']}
    entities, kinds, groups = [], [], {}
    def intern(table, value):
        if value not in table:
            table.append(value)
        return table.index(value)
    def endpoint(node, origin=None, *, target=False):
        if node.get('path'):
            return intern(entities, ['file', node['path']])
        path = (node.get('origin') or origin or {}).get('path')
        if target:
            return intern(entities, ['unresolved_targets', path])
        if path:
            return intern(entities, ['origin_file', path])
        return intern(entities, ['unresolved_symbol', node.get('name')])
    changes = ['retained', 'added', 'removed', 'unknown']
    for edge in graph['edges']:
        kind = intern(kinds, [edge['kind'], edge['relationship']])
        counted = set()
        for observation in edge['observations']:
            origin = observation.get('origin') or {}
            source = endpoint(by_id[observation['source']], origin)
            target = endpoint(by_id[observation['target']], origin, target=True)
            key = (source, target, kind)
            group = groups.setdefault(key, dict(counts=[0, 0, 0, 0], locations={}))
            if key not in counted:
                group['counts'][changes.index(edge['change'])] += 1
                counted.add(key)
            origin_id = intern(entities, ['file', origin['path']]) if origin.get('path') else None
            location_key = (1 if observation['snapshot'] == 'base' else 2, origin_id)
            group['locations'].setdefault(location_key, set()).add(origin.get('line'))
    def intervals(lines):
        result = []
        for line in sorted(value for value in lines if isinstance(value, int)):
            if result and result[-1][1] + 1 == line:
                result[-1][1] = line
            else:
                result.append([line, line])
        if None in lines:
            result.append([None, None])
        return result
    edges = [[*key, value['counts'], [[*location, intervals(lines)]
              for location, lines in value['locations'].items()]] for key, value in groups.items()]
    focus = set(focus_paths)
    selected_edges = [edge for edge in graph['edges'] if focus.intersection(edge['affectedPaths'])]
    selected_nodes = {observation[key] for edge in selected_edges for observation in edge['observations']
                      for key in ('source', 'target')}
    selected_nodes.update(node['id'] for node in graph['nodes'] if node.get('overlapsChangedLines')
                          and (not focus or node.get('path') in focus))
    detail = _symbol_graph_context({**graph, 'nodes': [node for node in graph['nodes'] if node['id'] in selected_nodes],
                                   'edges': selected_edges})
    return dict(scope=graph['scope'], snapshots=graph['snapshots'],
        encoding='Complete collected file relationship map. Entity indices reference [type, path]; origin_file locates unresolved source evidence. unresolved_targets groups unverified targets by evidence file, never a dependency on another file. Line intervals are inclusive. Snapshot 1=base, 2=proposed. Counts retain all collected relationships; symbolDetail expands exact symbols and unresolved names for focused files. Complete individual evidence is persisted and accessible through graph tools.',
        entities=entities, relationKinds=kinds, changes=changes,
        edgeColumns=['sourceEntityIndex', 'targetEntityIndex', 'relationKindIndex', 'countsByChange', 'locations'],
        locationColumns=['snapshot', 'originEntityIndex', 'lineIntervals'], edges=edges,
        symbolDetail=detail, coverage=graph['coverage'])


class ChangeGraph:
    def __init__(self, request, files, client=None):
        self.request, self.client = request, client
        self.files = {item['path']: item for item in files}
        self.seen = set()
        self.units = {'base': {}, 'proposed': {}}
        self.relations = {'base': {}, 'proposed': {}}
        self.queries = []
        self.snapshots = {}
        self.diagnostics = []
        self.completed = set()
        self._slots = asyncio.Semaphore(4)
        base = resolve_exact_structural_base_revision(request)
        self.base_binding = dict(workspace=request.projectWorkspace, project=request.projectNamespace,
            branch=request.targetBranchName, repository_revision=base,
            repository_generation_manifest_sha256=request.ragBaseGenerationManifestSha256,
            collection_target=request.ragCollectionTarget)
        self.review_binding = dict(workspace=request.projectWorkspace, project=request.projectNamespace,
            target_branch=request.targetBranchName, base_revision=base,
            source_revision=request.currentCommitHash or request.commitHash,
            target_repo_path=request.localRagRepoPath or request.localRepoPath,
            review_overlay_path=request.localReviewOverlayPath,
            base_collection_target=request.ragCollectionTarget,
            base_generation_revision=request.ragBaseGenerationRevision,
            base_generation_manifest_sha256=request.ragBaseGenerationManifestSha256,
            review_collection_target=request.ragReviewCollectionTarget,
            review_generation_manifest_sha256=request.ragReviewGenerationManifestSha256)
        if request.ragIndexPolicy:
            self.review_binding.update(request.ragIndexPolicy.model_dump())

    async def collect(self, paths):
        paths = [path for path in dict.fromkeys(paths) if path not in self.seen]
        self.seen.update(paths)
        if not paths:
            return
        if self.client is None or not self.request.ragEnabled:
            if not self.diagnostics:
                self.diagnostics.append('Repository graph unavailable; diff descriptions remain usable.')
            return
        # Parallel reads within a scan batch do not change sequential model scans.
        results = await asyncio.gather(*(self._collect_path(path, snapshot)
            for path in paths for snapshot in ('base', 'proposed')), return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                self.diagnostics.append(f'Graph query unavailable ({type(result).__name__}).')

    async def _collect_path(self, path, snapshot):
        item = self.files[path]
        query_path = item.get('oldPath') or path if snapshot == 'base' else path
        absent = (snapshot == 'base' and item.get('changeType') == 'added'
                  or snapshot == 'proposed' and item.get('changeType') == 'deleted')
        if absent:
            self.completed.add((path, snapshot))
            return
        binding = self.base_binding if snapshot == 'base' else self.review_binding
        required = ('workspace', 'project', 'branch', 'repository_revision', 'collection_target',
                    'repository_generation_manifest_sha256') if snapshot == 'base' else (
                    'workspace', 'project', 'target_branch', 'base_revision', 'source_revision',
                    'target_repo_path', 'review_overlay_path', 'review_collection_target',
                    'review_generation_manifest_sha256')
        if not all(isinstance(binding.get(key), str) and binding[key] for key in required):
            self.diagnostics.append(f'{snapshot} graph binding unavailable for {path}.')
            return
        async with self._slots:
            try:
                # The shared source pool bounds concurrency. Each HTTP request
                # owns its transport timeout; large files may need many pages.
                async with review_context_slot('stage_0_change_graph'):
                    for pattern in ('file_summary', 'relations_of'):
                        cursor = 0
                        while True:
                            arguments = dict(pattern=pattern, target=query_path, max_results=100,
                                             cursor=cursor, **binding)
                            if snapshot == 'base':
                                response = await self.client.query_code_graph(**arguments)
                            else:
                                response = await self.client.query_review_graph(**arguments,
                                    focus_paths=[path], include_source=False, detail_level='standard',
                                    token_budget=16000)
                            receipt = dict(path=path, snapshot=snapshot, pattern=pattern, cursor=cursor,
                                status=response.get('status', 'ready'),
                                returned=len(response.get('results') or []), nextCursor=response.get('nextCursor'),
                                coverage=response.get('coverage', {}))
                            self.queries.append(receipt)
                            observed = response.get('snapshot') or {}
                            expected = binding.get('repository_revision') if snapshot == 'base' else binding['source_revision']
                            actual = observed.get('revision') if snapshot == 'base' else observed.get('sourceRevision')
                            manifest = binding.get('repository_generation_manifest_sha256') if snapshot == 'base' else binding['review_generation_manifest_sha256']
                            if actual != expected or observed.get('generationManifestSha256') != manifest:
                                raise ValueError('graph response does not match request revision/generation')
                            if response.get('status') in {'unavailable', 'error', 'ambiguous'}:
                                raise ValueError('graph query did not resolve exact path')
                            self.snapshots[snapshot] = observed
                            for value in response.get('results', []):
                                if pattern == 'file_summary':
                                    self.units[snapshot][value['unitId']] = value
                                else:
                                    self.relations[snapshot][value['evidenceId']] = value
                                    for endpoint in ('sourceUnit', 'targetUnit'):
                                        unit = value.get(endpoint)
                                        if isinstance(unit, dict) and unit.get('unitId'):
                                            self.units[snapshot][unit['unitId']] = unit
                            next_cursor = response.get('nextCursor')
                            if next_cursor is None:
                                coverage = response.get('coverage') or {}
                                omissions = coverage.get('budgetOmissions') or {}
                                # These repeated resolution hints were already
                                # acquired exhaustively through file_summary.
                                hints_only = bool(omissions) and set(omissions) <= {'resolvedUnits'}
                                if response.get('truncated') or (coverage.get('truncated') and not hints_only):
                                    raise ValueError('graph response bounded without continuation')
                                break
                            if not isinstance(next_cursor, int) or next_cursor <= cursor:
                                raise ValueError('graph pagination made no progress')
                            cursor = next_cursor
                    self.completed.add((path, snapshot))
            except Exception as error:
                self.diagnostics.append(f'{snapshot} graph incomplete for {path} ({type(error).__name__}: {error}).')
                logger.warning('PR change graph: %s', self.diagnostics[-1])

    def render(self):
        nodes, edges = {}, {}
        aliases = {item.get('oldPath'): path for path, item in self.files.items() if item.get('oldPath')}
        def canonical(path):
            return aliases.get(path, path)
        def ranges_for(path, snapshot):
            item = self.files.get(canonical(path)) or {}
            return [r['base' if snapshot == 'base' else 'head'] for r in
                    item.get('changedRanges', item.get('ranges', []))]
        affected_units = {'base': set(), 'proposed': set()}
        for snapshot, units in self.units.items():
            by_path = {}
            for unit in units.values():
                by_path.setdefault(unit.get('path'), []).append(unit)
            for path, candidates in by_path.items():
                for unit in candidates:
                    start, end = unit.get('startLine'), unit.get('endLine')
                    if not isinstance(start, int) or not isinstance(end, int):
                        continue
                    intervals = [(max(start, first), min(end, first + max(count, 1) - 1))
                        for first, count in ranges_for(path, snapshot)
                        if start <= first + max(count, 1) - 1 and end >= first]
                    # A method-body edit does not make every method in its class
                    # or every symbol in its file a changed root.
                    for child in candidates:
                        lo, hi = child.get('startLine'), child.get('endLine')
                        if not isinstance(lo, int) or not isinstance(hi, int) or not (start <= lo <= hi <= end):
                            continue
                        if (lo, hi) == (start, end):
                            continue
                        intervals = [part for a, b in intervals for part in
                            ([(a, b)] if hi < a or lo > b else
                             [(a, min(b, lo - 1)), (max(a, hi + 1), b)]) if part[0] <= part[1]]
                    if intervals:
                        affected_units[snapshot].add(unit['unitId'])
        def affected(unit, snapshot):
            return bool(unit and unit.get('unitId') in affected_units[snapshot])
        affected_locations = {}
        for snapshot, units in self.units.items():
            for unit in units.values():
                if affected(unit, snapshot) and unit.get('kind') not in {'file', 'simplified'}:
                    affected_locations.setdefault((snapshot, unit.get('path')), []).append(
                        (unit['startLine'], unit['endLine'] - unit['startLine'] + 1))
        def node(unit, snapshot, label, origin=None, resolved_path=None):
            if unit:
                key = snapshot + ':' + unit['unitId']
                nodes[key] = {'id': key, **{k: unit[k] for k in
                    ('unitId', 'path', 'language', 'kind', 'name', 'qualifiedName', 'startLine', 'endLine', 'recordType') if k in unit},
                    'snapshot': snapshot, 'changedFile': canonical(unit.get('path')) in self.files,
                    'overlapsChangedLines': affected(unit, snapshot), 'resolution': 'indexed'}
                name = unit.get('qualifiedName') or unit.get('name') or ''
                name = name.replace(f":{unit.get('startLine')}-{unit.get('endLine')}:", ':')
                signature = [canonical(unit.get('path')), unit.get('kind'), name]
            else:
                key = snapshot + ':unresolved:' + _identity([label, origin, resolved_path])
                nodes[key] = dict(id=key, name=label, snapshot=snapshot, resolution='unresolved',
                                  origin=origin)
                if resolved_path:
                    nodes[key].update(path=resolved_path, resolution='file_only',
                                      changedFile=canonical(resolved_path) in self.files)
                signature = ['unresolved', label, canonical(resolved_path or (origin or {}).get('path'))]
            return key, signature
        for snapshot, units in self.units.items():
            for unit in units.values():
                if affected(unit, snapshot):
                    node(unit, snapshot, unit.get('name'))
            for relation in self.relations[snapshot].values():
                origin = relation.get('origin') or {}
                source, target = relation.get('sourceUnit'), relation.get('targetUnit')
                paths = {canonical(p) for p in relation.get('relatedPaths', [])}
                paths.update(canonical(u.get('path')) for u in (source, target) if u)
                paths.add(canonical(origin.get('path')))
                touched = sorted(p for p in paths if p in self.files)
                if not touched:
                    continue
                symbol_touched = any(affected(u, snapshot) and u.get('kind') not in {'file', 'simplified'}
                                     for u in (source, target) if u)
                origin_touched = _overlaps(origin.get('line'), origin.get('line'),
                                           ranges_for(origin.get('path'), snapshot))
                # An unresolved plugin endpoint can still have an exact location
                # inside a changed callable. Preserve that fact as unresolved.
                inside_changed_symbol = _overlaps(origin.get('line'), origin.get('line'),
                    affected_locations.get((snapshot, origin.get('path')), []))
                resolved_paths = [p for p in relation.get('relatedPaths', []) if p != origin.get('path')]
                # Import resolution can prove a file without proving a member
                # declaration (for example a dynamically imported class method).
                resolved_path = (resolved_paths[0] if len(resolved_paths) == 1 and relation.get('relation')
                                 in {'resolves-import', 'resolves-to', 'calls-resolved-target'} else None)
                # Keep incoming file-level consumers even when their imported
                # member cannot be resolved to one changed declaration.
                incoming_file = not target and canonical(resolved_path) in self.seen
                # File imports describe the dependency context even when an edit
                # lies inside a multiline import or only changes an imported use.
                file_import = (canonical(origin.get('path')) in self.files and
                               relation.get('relation') in {'imports', 'resolves-import', 'resolves-to'})
                if not (symbol_touched or origin_touched or inside_changed_symbol or incoming_file or file_import):
                    continue
                source_id, source_key = node(source, snapshot, relation.get('source'), origin)
                target_id, target_key = node(target, snapshot, relation.get('target'), resolved_path=resolved_path)
                key = _identity([relation.get('kind'), relation.get('relation'), source_key, target_key])
                edge = edges.setdefault(key, dict(id=key, kind=relation.get('kind'),
                    relationship=relation.get('relation'), affectedPaths=touched, observations=[]))
                edge['observations'].append(dict(snapshot=snapshot, source=source_id, target=target_id,
                    evidenceId=relation['evidenceId'], origin=origin, attributes=relation.get('attributes', {}),
                    relatedPaths=relation.get('relatedPaths', [])))
                edge['affectedPaths'] = sorted(set(edge['affectedPaths']) | set(touched))
        for edge in edges.values():
            seen = {o['snapshot'] for o in edge['observations']}
            complete = all((p, s) in self.completed for p in edge['affectedPaths'] for s in ('base', 'proposed'))
            edge['change'] = ('retained' if len(seen) == 2 else
                              ('added' if 'proposed' in seen else 'removed') if complete else 'unknown')
        unrepresented = []
        for path in sorted(self.seen):
            if not any(canonical(u.get('path')) == path for units in self.units.values() for u in units.values()):
                unrepresented.append(path)
        return dict(scope='Indexed dependency neighborhood of changed lines, symbols and files; external or dynamic targets may be unresolved.',
            snapshots=self.snapshots, nodes=list(nodes.values()), edges=list(edges.values()),
            coverage=dict(status='degraded' if self.diagnostics or unrepresented else 'complete',
                queriedPaths=sorted(self.seen), unrepresentedPaths=unrepresented,
                unresolvedNodes=sum(n['resolution'] == 'unresolved' for n in nodes.values()),
                fileOnlyNodes=sum(n['resolution'] == 'file_only' for n in nodes.values()),
                diagnostics=list(dict.fromkeys(self.diagnostics))), queries=self.queries)
