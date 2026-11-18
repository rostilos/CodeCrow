"""Revision-bound dependency evidence for the cumulative PR change scan.

Reads existing graph generations; it never builds indexes or asks a model to
invent structural facts. Query failures remain visible optional enrichment.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import posixpath

from service.review.snapshot_identity import resolve_exact_structural_base_revision
from service.review.execution_scheduler import review_context_slot

logger = logging.getLogger(__name__)


def _identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]


def _next_cursor(response, cursor):
    next_cursor = response.get('nextCursor')
    if next_cursor is None:
        coverage = response.get('coverage') or {}
        omissions = coverage.get('budgetOmissions') or {}
        # Resolution hints were already acquired exhaustively through file_summary.
        hints_only = bool(omissions) and set(omissions) <= {'resolvedUnits'}
        if response.get('truncated') or (coverage.get('truncated') and not hints_only):
            raise ValueError('graph response bounded without continuation')
    elif not isinstance(next_cursor, int) or next_cursor <= cursor:
        raise ValueError('graph pagination made no progress')
    return next_cursor


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


def graph_symbol_context(graph, focus_paths=()):
    focus = set(focus_paths)
    selected_edges = [edge for edge in graph['edges'] if focus.intersection(edge['affectedPaths'])]
    selected_nodes = {observation[key] for edge in selected_edges for observation in edge['observations']
                      for key in ('source', 'target')}
    selected_nodes.update(node['id'] for node in graph['nodes'] if node.get('overlapsChangedLines')
                          and (not focus or node.get('path') in focus))
    return _symbol_graph_context({**graph, 'nodes': [node for node in graph['nodes'] if node['id'] in selected_nodes],
                                  'edges': selected_edges})


def graph_prompt_context(graph, focus_paths=()):
    """Complete file dependency map plus exact symbol detail for the focus.

Aggregation retains every collected relation, its kind, revision and origin
file. Exact lines are expanded in focused symbol detail and the persisted graph.
Unresolved symbols stay distinct from real repository paths.
"""
    by_id = {node['id']: node for node in graph['nodes']}
    entities, kinds, groups = [], [], {}
    indices = {}
    def intern(table, value):
        key = (id(table), tuple(value))
        if key not in indices:
            indices[key] = len(table)
            table.append(value)
        return indices[key]
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
            mask = 1 if observation['snapshot'] == 'base' else 2
            group['locations'][origin_id] = group['locations'].get(origin_id, 0) | mask
    edges = []
    for key, value in groups.items():
        counts = value['counts']
        mask = (3 if counts[0] else 0) | (2 if counts[1] else 0) | (1 if counts[2] else 0)
        while counts and counts[-1] == 0:
            counts.pop()
        row = [*key, counts]
        if value['locations'] != {key[0]: mask}:
            row.append([[origin, snapshots] for origin, snapshots in value['locations'].items()])
        edges.append(row)
    paths = sorted({value for kind, value in entities if kind != 'unresolved_symbol' and value is not None})
    path_ids = {path: index for index, path in enumerate(paths)}
    entities = [[kind, path_ids[value] if kind != 'unresolved_symbol' and value is not None else value]
                for kind, value in entities]
    return dict(scope=graph['scope'], snapshots=graph['snapshots'],
        encoding='Full file graph. Entity=[type,pathIndex]; unresolved_symbol uses a literal name; null=no path. origin_file/unresolved_targets locate unresolved evidence, not verified dependencies. Snapshot masks: base=1,proposed=2,both=3. Trailing counts=0; absent origins=[sourceEntityIndex,mask from retained=3/added=2/removed=1]. Exact symbols/lines: symbolDetail and saved graph/tools.',
        paths=paths, entities=entities, relationKinds=kinds, changes=changes,
        edgeColumns=['sourceEntityIndex', 'targetEntityIndex', 'relationKindIndex', 'countsByChange', 'origins?'],
        locationColumns=['originEntityIndex', 'snapshotMask'], edges=edges,
        symbolDetail=graph_symbol_context(graph, focus_paths), coverage={**{k: v for k, v in graph['coverage'].items() if k != 'queriedPaths'},
            'queriedPathCount': len(graph['coverage'].get('queriedPaths', []))})


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
        self.omissions = []
        self.completed = set()
        self.file_coverage = {}
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

    def resolve_import_path(self, source_path, import_path):
        """Use plugin-proven import bindings, never filename/extension guesses."""
        if isinstance(import_path, str) and import_path.startswith(('./', '../')):
            import_path = posixpath.normpath(posixpath.join(posixpath.dirname(source_path), import_path))
        snapshot = 'base' if self.files.get(source_path, {}).get('changeType') == 'deleted' else 'proposed'
        matches = {}
        for relation in self.relations[snapshot].values():
            if (relation.get('relation') != 'resolves-import'
                    or (relation.get('origin') or {}).get('path') != source_path):
                continue
            specifier = (relation.get('attributes') or {}).get('specifier')
            if not isinstance(specifier, str):
                continue
            resolved_spelling = (posixpath.normpath(posixpath.join(posixpath.dirname(source_path), specifier))
                                 if specifier.startswith('.') else specifier)
            targets = set(relation.get('relatedPaths', [])) - {source_path}
            if import_path not in {specifier, resolved_spelling, *targets}:
                continue
            if import_path not in {specifier, resolved_spelling}:
                bound_targets = {path for other in self.relations[snapshot].values()
                    if other.get('relation') == 'resolves-import'
                    and (other.get('origin') or {}).get('path') == source_path
                    and (other.get('attributes') or {}).get('specifier') == specifier
                    for path in other.get('relatedPaths', []) if path != source_path}
                if bound_targets != {import_path}:
                    continue
            if len(targets) == 1:
                target = next(iter(targets))
                matches.setdefault(target, set()).add(relation['evidenceId'])
        if len(matches) != 1:
            return None
        path, evidence = next(iter(matches.items()))
        return dict(path=path, snapshot=snapshot, evidenceIds=sorted(evidence))

    def resolve_import_extension(self, source_path, requested):
        """Repair an inferred filename extension from one existing import target."""
        if requested.startswith(('./', '../')):
            requested = posixpath.normpath(posixpath.join(posixpath.dirname(source_path), requested))
        stem, extension = posixpath.splitext(requested)
        if (not extension or requested.startswith(('/', '../')) or '\\' in requested
                or posixpath.normpath(requested) != requested):
            return None
        snapshot = 'base' if self.files.get(source_path, {}).get('changeType') == 'deleted' else 'proposed'
        candidates = {path for relation in self.relations[snapshot].values()
            if relation.get('relation') == 'resolves-import'
            and (relation.get('origin') or {}).get('path') == source_path
            for path in relation.get('relatedPaths', [])
            if path not in {source_path, requested} and posixpath.splitext(path)[0] == stem}
        if len(candidates) != 1:
            return None
        return self.resolve_import_path(source_path, candidates.pop())

    def import_reference(self, source_path, module):
        """Retain a source-proven module name or import pattern without asserting a file."""
        if not module:
            return None
        snapshot = 'base' if self.files.get(source_path, {}).get('changeType') == 'deleted' else 'proposed'
        patterns = sorted({relation['evidenceId'] for relation in self.relations[snapshot].values()
            if relation.get('relation') == 'imports-pattern'
            and (relation.get('origin') or {}).get('path') == source_path
            and relation.get('target') == module})
        if patterns:
            return dict(kind='module_pattern', resolution='unresolved', snapshot=snapshot, evidenceIds=patterns)
        pattern = (posixpath.normpath(posixpath.join(posixpath.dirname(source_path), module))
                   if module.startswith('./') else module)
        declarations = sorted({relation['evidenceId'] for relation in self.relations[snapshot].values()
            if relation.get('relation') == 'declares-file-pattern'
            and (relation.get('origin') or {}).get('path') == source_path
            and relation.get('source') == source_path
            and (relation.get('target') == pattern
                 or (relation.get('attributes') or {}).get('targetSpecifier') == module
                 or (relation.get('attributes') or {}).get('exportKey') == module)})
        if declarations:
            return dict(kind='file_pattern', resolution='unresolved', snapshot=snapshot, evidenceIds=declarations)
        if module.startswith('/'):
            return None
        evidence = sorted({relation['evidenceId'] for relation in self.relations[snapshot].values()
            if relation.get('relation') in {'imports', 'declares-dependency', 'configures-module'}
            and (relation.get('origin') or {}).get('path') == source_path
            and (relation.get('target') == module
                 or relation.get('relation') == 'configures-module'
                 and (relation.get('attributes') or {}).get('targetSpecifier') == module
                 or relation.get('relation') == 'imports'
                 and str(relation.get('target', '')).startswith(module + '::'))})
        if evidence:
            return dict(kind='module', resolution='unresolved', snapshot=snapshot, evidenceIds=evidence)
        return None

    def directory_reference(self, source_path, directory):
        """Keep an exact source-bound directory declaration without asserting files exist."""
        if not directory:
            return None
        snapshot = 'base' if self.files.get(source_path, {}).get('changeType') == 'deleted' else 'proposed'
        evidence = sorted({relation['evidenceId'] for relation in self.relations[snapshot].values()
            if relation.get('relation') == 'declares-directory'
            and (relation.get('origin') or {}).get('path') == source_path
            and relation.get('source') == source_path
            and (relation.get('target') == directory
                 or (relation.get('attributes') or {}).get('targetSpecifier') == directory)})
        if evidence:
            return dict(kind='directory', resolution='unresolved', snapshot=snapshot, evidenceIds=evidence)
        return None

    def removed_import_reference(self, source_path, module):
        """Retain an import removed from a modified file, with its base origin."""
        item = self.files.get(source_path, {})
        if (not module or module.startswith('/')
                or item.get('changeType') not in {'modified', 'renamed'}
                or not all((source_path, snapshot) in self.completed for snapshot in ('base', 'proposed'))
                or self.import_reference(source_path, module)):
            return None
        ranges = [row['base'] for row in item.get('changedRanges', []) if row['base'][1] > 0]
        evidence = sorted({relation['evidenceId'] for relation in self.relations['base'].values()
            if relation.get('relation') == 'imports'
            and (relation.get('origin') or {}).get('path') == (item.get('oldPath') or source_path)
            and (relation.get('target') == module or str(relation.get('target', '')).startswith(module + '::'))
            and _overlaps((relation.get('origin') or {}).get('line'), None, ranges)})
        if evidence:
            return dict(kind='module', resolution='unresolved', snapshot='base', evidenceIds=evidence)
        return None

    def resolve_relative_import_prefix(self, source_path, requested):
        """Repair only dot-prefix drift using one source-bound import binding."""
        def remainder(value):
            if not isinstance(value, str) or not value.startswith(('./', '../')):
                return None
            while value.startswith(('./', '../')):
                value = value.split('/', 1)[1]
            return value or None
        suffix = remainder(requested)
        if suffix is None:
            return None
        snapshot = 'base' if self.files.get(source_path, {}).get('changeType') == 'deleted' else 'proposed'
        candidates = {(relation.get('attributes') or {}).get('specifier')
            for relation in self.relations[snapshot].values()
            if relation.get('relation') == 'resolves-import'
            and (relation.get('origin') or {}).get('path') == source_path
            and remainder((relation.get('attributes') or {}).get('specifier')) == suffix}
        if len(candidates) != 1:
            return None
        specifier, = candidates
        if specifier == requested:
            return None
        resolved = self.resolve_import_path(source_path, specifier)
        return {**resolved, 'specifier': specifier} if resolved else None

    def resolve_dependency_source(self, module, paths):
        """Find one declaring file among the caller's currently described paths."""
        matches = []
        for path in paths:
            snapshot = 'base' if self.files.get(path, {}).get('changeType') == 'deleted' else 'proposed'
            evidence = sorted({relation['evidenceId'] for relation in self.relations[snapshot].values()
                if relation.get('relation') == 'declares-dependency'
                and relation.get('target') == module
                and (relation.get('origin') or {}).get('path') == path})
            if evidence:
                matches.append(dict(path=path, snapshot=snapshot, evidenceIds=evidence))
        return matches[0] if len(matches) == 1 else None

    async def matching_paths(self, fragment, source_path):
        """Find every suffix candidate in the same sealed snapshot as the source."""
        if self.client is None or not self.request.ragEnabled:
            return None
        snapshot = 'base' if self.files.get(source_path, {}).get('changeType') == 'deleted' else 'proposed'
        if not self._has_binding(snapshot):
            return None
        paths, cursor = set(), 0
        try:
            async with self._slots, review_context_slot('stage_0_change_graph'):
                while True:
                    response = await self._query_page(fragment, snapshot, 'file_search', cursor)
                    paths.update(value['path'] for value in response.get('results', [])
                                 if value.get('path') == fragment or
                                 str(value.get('path', '')).endswith('/' + fragment))
                    cursor = _next_cursor(response, cursor)
                    if cursor is None:
                        return paths
        except Exception as error:
            self.diagnostics.append(f'{snapshot} path lookup incomplete for {fragment} ({type(error).__name__}: {error}).')
            logger.warning('PR change graph: %s', self.diagnostics[-1])
            return None

    async def resolve_file_alias(self, source_path, alias):
        """Navigate a unique declared alias in the source's bound repository snapshot."""
        if self.client is None or not self.request.ragEnabled:
            return None
        snapshot = 'base' if self.files.get(source_path, {}).get('changeType') == 'deleted' else 'proposed'
        if not self._has_binding(snapshot):
            return None
        local_alias = alias == '.' or alias.startswith('./')
        matches, packages, facts, cursor = {}, {}, {}, 0
        try:
            async with self._slots, review_context_slot('stage_0_change_graph'):
                while True:
                    response = await self._query_page(source_path if local_alias else alias,
                                                     snapshot, 'relations_of', cursor, path=source_path)
                    for fact in response.get('results', []):
                        target = fact.get('target')
                        kind = fact.get('relation')
                        origin = (fact.get('origin') or {}).get('path')
                        alias_matches = fact.get('source') == alias
                        if local_alias:
                            # Declaration keys have meaning only in their owning source file.
                            alias_matches = origin == source_path and (alias_matches or
                                kind == 'declares-file-alias' and (fact.get('attributes') or {}).get('exportKey') == alias)
                        if (kind in {'declares-file-alias', 'declares-package'} and alias_matches
                                and isinstance(target, str) and target and not target.startswith(('/', '../'))
                                and posixpath.normpath(target) == target
                                and target in fact.get('relatedPaths', [])
                                and origin and fact.get('evidenceId')
                                and (kind != 'declares-package' or origin == target)):
                            candidates = packages if kind == 'declares-package' else matches
                            candidates.setdefault(target, set()).add(fact['evidenceId'])
                            facts[fact['evidenceId']] = fact
                    cursor = _next_cursor(response, cursor)
                    if cursor is None:
                        break
            # A package identity names its manifest, independently of its runtime exports.
            matches = packages or matches
            if len(matches) != 1:
                return None
            target, evidence = next(iter(matches.items()))
            if target not in (await self.matching_paths(target, source_path) or set()):
                return None
            self.relations[snapshot].update(facts)
            for fact in facts.values():
                for endpoint in ('sourceUnit', 'targetUnit'):
                    unit = fact.get(endpoint)
                    if isinstance(unit, dict) and unit.get('unitId'):
                        self.units[snapshot][unit['unitId']] = unit
            result = dict(path=target, snapshot=snapshot, evidenceIds=sorted(evidence))
            if packages:
                result['referenceKind'] = 'package_manifest'
            return result
        except Exception as error:
            self.diagnostics.append(f'{snapshot} alias lookup incomplete for {alias} ({type(error).__name__}: {error}).')
            logger.warning('PR change graph: %s', self.diagnostics[-1])
            return None

    def _has_binding(self, snapshot):
        binding = self.base_binding if snapshot == 'base' else self.review_binding
        required = ('workspace', 'project', 'branch', 'repository_revision', 'collection_target',
                    'repository_generation_manifest_sha256') if snapshot == 'base' else (
                    'workspace', 'project', 'target_branch', 'base_revision', 'source_revision',
                    'target_repo_path', 'review_overlay_path', 'review_collection_target',
                    'review_generation_manifest_sha256')
        return all(isinstance(binding.get(key), str) and binding[key] for key in required)

    async def _query_page(self, target, snapshot, pattern, cursor, *, path=None):
        focus = path or target
        if (not focus or focus in {'.', '..'} or focus.startswith(('/', '../'))
                or '\\' in focus or posixpath.normpath(focus) != focus):
            # Model relationships can name routes or symbols rather than files.
            omission = dict(path=focus, reason='not_repository_file_path')
            if omission not in self.omissions:
                self.omissions.append(omission)
            return dict(status='not_applicable', results=[])
        binding = self.base_binding if snapshot == 'base' else self.review_binding
        if not self._has_binding(snapshot):
            raise ValueError(f'{snapshot} graph binding unavailable for {target}')
        arguments = dict(pattern=pattern, target=target, max_results=100, cursor=cursor, **binding)
        if snapshot == 'base':
            response = await self.client.query_code_graph(**arguments)
        else:
            response = await self.client.query_review_graph(**arguments,
                focus_paths=[path or target], include_source=False, detail_level='standard', token_budget=16000)
        self.queries.append(dict(path=path or target, snapshot=snapshot, pattern=pattern, cursor=cursor,
            status=response.get('status', 'ready'), returned=len(response.get('results') or []),
            nextCursor=response.get('nextCursor'), coverage=response.get('coverage', {}),
            fileCoverage=response.get('fileCoverage')))
        if response.get('status') in {'unavailable', 'error', 'ambiguous'}:
            raise ValueError(f"graph query failed ({response.get('status_code') or response['status']}): "
                             f"{response.get('error') or 'exact path not resolved'}")
        observed = response.get('snapshot') or {}
        expected = binding.get('repository_revision') if snapshot == 'base' else binding['source_revision']
        actual = observed.get('revision') if snapshot == 'base' else observed.get('sourceRevision')
        manifest = binding.get('repository_generation_manifest_sha256') if snapshot == 'base' else binding['review_generation_manifest_sha256']
        if actual != expected or observed.get('generationManifestSha256') != manifest:
            raise ValueError('graph response does not match request revision/generation')
        self.snapshots[snapshot] = observed
        return response

    async def collect(self, paths):
        paths = [path for path in dict.fromkeys(paths) if path not in self.seen]
        self.seen.update(paths)
        if not paths:
            return
        if self.client is None or not self.request.ragEnabled:
            if not self.omissions:
                self.omissions.append(dict(reason='graph_disabled' if not self.request.ragEnabled
                                           else 'graph_not_configured'))
            return
        # Parallel reads within a scan batch do not change sequential model scans.
        results = await asyncio.gather(*(self._collect_path(path, snapshot)
            for path in paths for snapshot in ('base', 'proposed')), return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                self.diagnostics.append(f'Graph query unavailable ({type(result).__name__}).')

    async def _collect_path(self, path, snapshot):
        item = self.files.get(path, {})
        query_path = item.get('oldPath') or path if snapshot == 'base' else path
        absent = (snapshot == 'base' and item.get('changeType') == 'added'
                  or snapshot == 'proposed' and item.get('changeType') == 'deleted')
        if absent:
            self.completed.add((path, snapshot))
            return
        if not self._has_binding(snapshot):
            omission = dict(snapshot=snapshot, reason='snapshot_not_available')
            if omission not in self.omissions:
                self.omissions.append(omission)
            return
        async with self._slots:
            try:
                # The shared source pool bounds concurrency. Each HTTP request
                # owns its transport timeout; large files may need many pages.
                async with review_context_slot('stage_0_change_graph'):
                    for pattern in ('file_summary', 'relations_of'):
                        cursor = 0
                        while True:
                            response = await self._query_page(query_path, snapshot, pattern, cursor, path=path)
                            if pattern == 'file_summary' and isinstance(response.get('fileCoverage'), dict):
                                self.file_coverage[(path, snapshot)] = response['fileCoverage']
                            for value in response.get('results', []):
                                if pattern == 'file_summary':
                                    self.units[snapshot][value['unitId']] = value
                                else:
                                    self.relations[snapshot][value['evidenceId']] = value
                                    for endpoint in ('sourceUnit', 'targetUnit'):
                                        unit = value.get(endpoint)
                                        if isinstance(unit, dict) and unit.get('unitId'):
                                            self.units[snapshot][unit['unitId']] = unit
                            next_cursor = _next_cursor(response, cursor)
                            if next_cursor is None:
                                break
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
                # Imports and package identities remain file context when edits change their use.
                file_context = (canonical(origin.get('path')) in self.files and
                                relation.get('relation') in {'imports', 'resolves-import', 'resolves-to', 'declares-package', 'declares-directory'})
                if not (symbol_touched or origin_touched or inside_changed_symbol or incoming_file or file_context):
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
        unrepresented, not_applicable = [], []
        for path in sorted(self.seen):
            if not any(canonical(u.get('path')) == path for units in self.units.values() for u in units.values()):
                item = self.files.get(path, {})
                reasons = []
                for snapshot in ('base', 'proposed'):
                    if ((snapshot == 'base' and item.get('changeType') == 'added') or
                            (snapshot == 'proposed' and item.get('changeType') == 'deleted')):
                        continue
                    coverage = self.file_coverage.get((path, snapshot), {})
                    reason = ('binary' if item.get('binary') else 'gitlink' if item.get('gitlink')
                              else coverage.get('state'))
                    if (path, snapshot) not in self.completed or reason not in {'binary', 'gitlink', 'empty', 'excluded'}:
                        reasons = []
                        break
                    reasons.append({'snapshot': snapshot, 'reason': reason})
                if item and reasons:
                    not_applicable.append({'path': path, 'observations': reasons})
                else:
                    unrepresented.append(path)
        if self.diagnostics:
            logger.warning('PR change graph status=degraded: unrepresented_paths=%s diagnostics=%s',
                           unrepresented, list(dict.fromkeys(self.diagnostics)))
        elif unrepresented or self.omissions:
            logger.info('Optional PR graph context omitted for %d path(s); source review continues', len(unrepresented))
        coverage_status = ('degraded' if self.diagnostics else
                           ('partial' if nodes or edges else 'unavailable')
                           if unrepresented or self.omissions else 'complete')
        return dict(scope='Indexed dependency neighborhood of changed lines, symbols and files; external or dynamic targets may be unresolved.',
            snapshots=self.snapshots, nodes=list(nodes.values()), edges=list(edges.values()),
            coverage=dict(status=coverage_status,
                queriedPaths=sorted(self.seen), unrepresentedPaths=unrepresented,
                omissions=list(self.omissions),
                nonStructuralPaths=not_applicable,
                unresolvedNodes=sum(n['resolution'] == 'unresolved' for n in nodes.values()),
                fileOnlyNodes=sum(n['resolution'] == 'file_only' for n in nodes.values()),
                diagnostics=list(dict.fromkeys(self.diagnostics))), queries=self.queries)
