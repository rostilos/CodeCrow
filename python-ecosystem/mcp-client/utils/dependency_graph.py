"""
Dependency graph builder for intelligent file batching.

SMART APPROACH: Leverages RAG's pre-indexed tree-sitter metadata to discover
file relationships instead of re-parsing diffs with regex.

The RAG system already has:
- semantic_names: function/method/class names  
- imports: import statements parsed by tree-sitter
- extends: parent classes/interfaces
- parent_class: containing class
- namespace: package/namespace

This module queries RAG to build a relationship graph, enabling intelligent
batching that keeps related files together for better cross-file context.
"""
import logging
from collections import defaultdict
from typing import Dict, List, Set, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from service.rag.rag_client import RagClient

from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass 
class FileNode:
    """Represents a file in the dependency graph."""
    path: str
    priority: str
    # Relationships discovered from RAG tree-sitter metadata
    related_files: Set[str] = field(default_factory=set)
    imports_symbols: Set[str] = field(default_factory=set)
    exports_symbols: Set[str] = field(default_factory=set)
    parent_classes: Set[str] = field(default_factory=set)
    namespaces: Set[str] = field(default_factory=set)
    extends: Set[str] = field(default_factory=set)
    focus_areas: List[str] = field(default_factory=list)
    relationship_strength: float = 0.0
    
    
@dataclass
class FileRelationship:
    """Represents a relationship between two files."""
    source_file: str
    target_file: str
    relationship_type: str  # 'definition', 'same_class', 'same_namespace'
    matched_on: str
    strength: float


class DependencyGraphBuilder:
    """
    Builds a dependency graph using RAG's tree-sitter metadata or pre-computed relationships.
    
    SMART APPROACH (v2): When Java sends enrichment data with pre-computed relationships,
    use those directly instead of querying RAG. This eliminates duplicate work since
    Java already called RAG's /parse endpoint.
    
    Fallback: When no enrichment data available, query RAG's deterministic context API
    which has the FULL file indexed with tree-sitter metadata.
    
    Relationship types discovered:
    - definition: File A uses symbol defined in File B
    - same_class: Files contain methods of the same class
    - same_namespace: Files are in the same package/namespace
    """
    
    RELATIONSHIP_WEIGHTS = {
        'changed_file': 1.0,
        'definition': 0.95,
        'IMPORTS': 0.90,
        'EXTENDS': 0.95,
        'IMPLEMENTS': 0.95,
        'CALLS': 0.85,
        'class_context': 0.85,
        'namespace_context': 0.75,
        'SAME_PACKAGE': 0.60,
    }
    
    def __init__(self, rag_client: Optional["RAGClient"] = None):
        self.rag_client = rag_client
        self.nodes: Dict[str, FileNode] = {}
        self.relationships: List[FileRelationship] = []
        self._metadata_cache: Dict[str, Dict] = {}
        
    def build_graph_from_enrichment(
        self,
        file_groups: List[Any],
        enrichment_data: Any,
    ) -> Dict[str, FileNode]:
        """
        Build dependency graph from pre-computed enrichment data sent by Java.
        
        This is the preferred method when enrichment_data is available, as it:
        - Eliminates redundant RAG calls (Java already parsed the files)
        - Uses full file content for accurate relationship detection
        - Provides relationships computed with proper AST parsing
        
        Args:
            file_groups: List of FileGroup objects with files to analyze
            enrichment_data: PrEnrichmentDataDto from Java with relationships and metadata
            
        Returns:
            Dict of file paths to FileNode objects with relationships populated
        """
        if not enrichment_data or not enrichment_data.has_data():
            logger.info("No enrichment data available, falling back to basic grouping")
            return self._build_basic_graph(file_groups)
        
        # Initialize nodes from file groups
        for group in file_groups:
            for f in group.files:
                self.nodes[f.path] = FileNode(
                    path=f.path,
                    priority=group.priority,
                    focus_areas=f.focus_areas if hasattr(f, 'focus_areas') else []
                )
        
        # Process pre-computed relationships
        relationships_by_file: Dict[str, Set[str]] = defaultdict(set)
        
        for rel in enrichment_data.relationships:
            source = rel.sourceFile
            target = rel.targetFile
            rel_type = rel.relationshipType.value if hasattr(rel.relationshipType, 'value') else str(rel.relationshipType)
            
            # Only add relationships between files we're analyzing
            if source in self.nodes and target in self.nodes:
                relationships_by_file[source].add(target)
                relationships_by_file[target].add(source)
                
                weight = self.RELATIONSHIP_WEIGHTS.get(rel_type, 0.5)
                self.relationships.append(FileRelationship(
                    source_file=source,
                    target_file=target,
                    relationship_type=rel_type,
                    matched_on=rel.matchedOn or "",
                    strength=weight
                ))
        
        # Process metadata to populate node symbols
        for meta in enrichment_data.fileMetadata:
            if meta.path in self.nodes:
                node = self.nodes[meta.path]
                if meta.imports:
                    node.imports_symbols.update(meta.imports)
                if meta.semanticNames:
                    node.exports_symbols.update(meta.semanticNames)
                if meta.extendsClasses:
                    node.extends.update(meta.extendsClasses)
                if meta.parentClass:
                    node.parent_classes.add(meta.parentClass)
                if meta.namespace:
                    node.namespaces.add(meta.namespace)
        
        # Update nodes with discovered relationships
        for file_path, related in relationships_by_file.items():
            if file_path in self.nodes:
                self.nodes[file_path].related_files.update(related)
                self.nodes[file_path].relationship_strength = self._calculate_strength(
                    file_path, related
                )
        
        logger.info(
            f"Dependency graph built from enrichment: {len(self.nodes)} files, "
            f"{len(self.relationships)} relationships"
        )
        
        return self.nodes
        
    def build_graph_from_rag(
        self,
        file_groups: List[Any],
        workspace: str,
        project: str,
        branches: List[str],
    ) -> Dict[str, FileNode]:
        """
        Build dependency graph by querying RAG's deterministic context API.
        
        This leverages tree-sitter metadata extracted during indexing:
        - imports, extends, parent_class, namespace, semantic_names
        """
        if not self.rag_client:
            logger.warning("No RAG client provided, falling back to basic grouping")
            return self._build_basic_graph(file_groups)
        
        # Collect all file paths
        all_file_paths = []
        file_priority_map = {}
        file_info_map = {}
        
        for group in file_groups:
            for f in group.files:
                all_file_paths.append(f.path)
                file_priority_map[f.path] = group.priority
                file_info_map[f.path] = f
                self.nodes[f.path] = FileNode(
                    path=f.path,
                    priority=group.priority,
                    focus_areas=f.focus_areas if hasattr(f, 'focus_areas') else []
                )
        
        if not all_file_paths:
            return self.nodes
        
        # Query RAG for deterministic context
        try:
            rag_response = self.rag_client.get_deterministic_context(
                workspace=workspace,
                project=project,
                branches=branches,
                file_paths=all_file_paths,
                limit_per_file=15
            )
            self._metadata_cache['last_response'] = rag_response
        except Exception as e:
            logger.warning(f"RAG query failed, falling back to basic grouping: {e}")
            return self._build_basic_graph(file_groups)
        
        # Extract relationships from RAG response
        self._extract_relationships_from_rag(rag_response, all_file_paths)
        
        logger.info(
            f"Dependency graph built: {len(self.nodes)} files, "
            f"{len(self.relationships)} relationships"
        )
        
        return self.nodes
    
    def _extract_relationships_from_rag(
        self, 
        rag_response: Dict,
        changed_file_paths: List[str]
    ) -> None:
        """Extract file relationships from RAG deterministic context response."""
        changed_file_set = set(changed_file_paths)
        file_relationships: Dict[str, Set[str]] = defaultdict(set)
        
        # Process changed_files to extract metadata
        changed_files = rag_response.get('changed_files', {})
        for file_path, chunks in changed_files.items():
            norm_path = file_path.lstrip('/')
            if norm_path in self.nodes:
                for chunk in chunks:
                    metadata = chunk.get('metadata', {})
                    
                    # Extract symbols this file exports (defines)
                    if metadata.get('primary_name'):
                        self.nodes[norm_path].exports_symbols.add(metadata['primary_name'])
                    if metadata.get('semantic_names'):
                        self.nodes[norm_path].exports_symbols.update(metadata['semantic_names'])
                    
                    # Extract what this file imports
                    if metadata.get('imports'):
                        for imp in metadata['imports']:
                            if isinstance(imp, str):
                                parts = imp.replace(';', '').split('\\')
                                if parts:
                                    self.nodes[norm_path].imports_symbols.add(parts[-1].strip())
                    
                    # Track class/namespace membership
                    if metadata.get('parent_class'):
                        self.nodes[norm_path].parent_classes.add(metadata['parent_class'])
                    if metadata.get('namespace'):
                        self.nodes[norm_path].namespaces.add(metadata['namespace'])
                    if metadata.get('extends'):
                        self.nodes[norm_path].extends.update(metadata['extends'])
        
        # Process related_definitions
        related_definitions = rag_response.get('related_definitions', {})
        for symbol, chunks in related_definitions.items():
            for chunk in chunks:
                metadata = chunk.get('metadata', {})
                related_path = metadata.get('path', '').lstrip('/')
                
                if related_path and related_path in self.nodes:
                    for file_path in changed_file_set:
                        norm_path = file_path.lstrip('/')
                        if norm_path in self.nodes:
                            node = self.nodes[norm_path]
                            if symbol in node.imports_symbols or symbol in node.exports_symbols:
                                file_relationships[norm_path].add(related_path)
                                file_relationships[related_path].add(norm_path)
                                self.relationships.append(FileRelationship(
                                    source_file=norm_path,
                                    target_file=related_path,
                                    relationship_type='definition',
                                    matched_on=symbol,
                                    strength=self.RELATIONSHIP_WEIGHTS['definition']
                                ))
        
        # Process class_context
        class_context = rag_response.get('class_context', {})
        for parent_class, chunks in class_context.items():
            class_files = set()
            for chunk in chunks:
                metadata = chunk.get('metadata', {})
                related_path = metadata.get('path', '').lstrip('/')
                if related_path in self.nodes:
                    class_files.add(related_path)
            
            for f1 in class_files:
                for f2 in class_files:
                    if f1 != f2:
                        file_relationships[f1].add(f2)
                        if f1 < f2:
                            self.relationships.append(FileRelationship(
                                source_file=f1,
                                target_file=f2,
                                relationship_type='same_class',
                                matched_on=parent_class,
                                strength=self.RELATIONSHIP_WEIGHTS['class_context']
                            ))
        
        # Process namespace_context
        namespace_context = rag_response.get('namespace_context', {})
        for namespace, chunks in namespace_context.items():
            ns_files = set()
            for chunk in chunks:
                metadata = chunk.get('metadata', {})
                related_path = metadata.get('path', '').lstrip('/')
                if related_path in self.nodes:
                    ns_files.add(related_path)
            
            for f1 in ns_files:
                for f2 in ns_files:
                    if f1 != f2:
                        file_relationships[f1].add(f2)
                        if f1 < f2:
                            self.relationships.append(FileRelationship(
                                source_file=f1,
                                target_file=f2,
                                relationship_type='same_namespace',
                                matched_on=namespace,
                                strength=self.RELATIONSHIP_WEIGHTS['namespace_context']
                            ))
        
        # Update nodes with discovered relationships
        for file_path, related in file_relationships.items():
            if file_path in self.nodes:
                self.nodes[file_path].related_files.update(related)
                self.nodes[file_path].relationship_strength = self._calculate_strength(
                    file_path, related
                )
    
    def _calculate_strength(self, file_path: str, related_files: Set[str]) -> float:
        total_strength = 0.0
        for rel in self.relationships:
            if rel.source_file == file_path or rel.target_file == file_path:
                total_strength += rel.strength
        return min(total_strength, 5.0)
    
    def _build_basic_graph(self, file_groups: List[Any]) -> Dict[str, FileNode]:
        """Fallback: build basic graph without RAG (by directory)."""
        for group in file_groups:
            for f in group.files:
                self.nodes[f.path] = FileNode(
                    path=f.path,
                    priority=group.priority,
                    focus_areas=f.focus_areas if hasattr(f, 'focus_areas') else []
                )
        
        # Files in same directory are related
        dir_files: Dict[str, List[str]] = defaultdict(list)
        for path in self.nodes:
            dir_path = '/'.join(path.split('/')[:-1]) if '/' in path else ''
            dir_files[dir_path].append(path)
        
        for dir_path, files in dir_files.items():
            if len(files) > 1:
                for f1 in files:
                    for f2 in files:
                        if f1 != f2:
                            self.nodes[f1].related_files.add(f2)
        
        return self.nodes
    
    def get_connected_components(self) -> List[Set[str]]:
        """Find connected components in the dependency graph."""
        visited = set()
        components = []
        
        def dfs(node_path: str, component: Set[str]):
            if node_path in visited:
                return
            visited.add(node_path)
            component.add(node_path)
            
            node = self.nodes.get(node_path)
            if not node:
                return
            
            for related_path in node.related_files:
                if related_path in self.nodes:
                    dfs(related_path, component)
        
        for path in self.nodes:
            if path not in visited:
                component: Set[str] = set()
                dfs(path, component)
                if component:
                    components.append(component)
        
        return components
    
    def get_smart_batches(
        self, 
        file_groups: List[Any], 
        workspace: str,
        project: str,
        branches: List[str],
        max_batch_size: int = 7,
        min_batch_size: int = 3,
        enrichment_data: Any = None
    ) -> List[List[Dict[str, Any]]]:
        """
        Create intelligent batches that keep related files together.
        
        Strategy:
        1. If enrichment_data is available, use pre-computed relationships from Java
        2. Otherwise, query RAG to discover file relationships via tree-sitter metadata
        3. Find connected components (files that are related)
        4. Batch files within components together
        5. For large components, split by priority while keeping related files together
        
        Args:
            file_groups: List of FileGroup objects with files
            workspace: Repository workspace/owner
            project: Repository slug
            branches: Branch names for context
            max_batch_size: Maximum files per batch
            min_batch_size: Minimum files per batch
            enrichment_data: Optional PrEnrichmentDataDto from Java with pre-computed relationships
        """
        # Use enrichment data if available, otherwise fall back to RAG
        if enrichment_data and hasattr(enrichment_data, 'has_data') and enrichment_data.has_data():
            logger.info("Using pre-computed enrichment data for dependency graph")
            self.build_graph_from_enrichment(file_groups, enrichment_data)
        else:
            self.build_graph_from_rag(file_groups, workspace, project, branches)
        components = self.get_connected_components()
        
        logger.info(
            f"Dependency analysis: {len(self.nodes)} files, "
            f"{len(components)} connected components, "
            f"{len(self.relationships)} relationships"
        )
        
        file_priority_map = {}
        file_info_map = {}
        for group in file_groups:
            for f in group.files:
                file_priority_map[f.path] = group.priority
                file_info_map[f.path] = f
        
        batches = []
        processed_files = set()
        priority_order = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW']
        
        def component_sort_key(comp):
            max_priority = min(
                priority_order.index(file_priority_map.get(f, 'LOW'))
                for f in comp
            )
            return (-len(comp), max_priority)
        
        for component in sorted(components, key=component_sort_key):
            if all(f in processed_files for f in component):
                continue
            
            component_files = [f for f in component if f not in processed_files]
            if not component_files:
                continue
            
            component_files_sorted = sorted(
                component_files,
                key=lambda f: (
                    -self.nodes[f].relationship_strength,
                    priority_order.index(file_priority_map.get(f, 'LOW')),
                    f
                )
            )
            
            current_batch = []
            for file_path in component_files_sorted:
                file_info = file_info_map.get(file_path)
                if not file_info:
                    continue
                
                node = self.nodes[file_path]
                current_batch.append({
                    "file": file_info,
                    "priority": file_priority_map.get(file_path, 'MEDIUM'),
                    "has_relationships": len(node.related_files) > 0,
                    "relationship_strength": node.relationship_strength,
                    "related_in_batch": [
                        r for r in node.related_files 
                        if r in {b['file'].path for b in current_batch}
                    ]
                })
                processed_files.add(file_path)
                
                if len(current_batch) >= max_batch_size:
                    batches.append(current_batch)
                    current_batch = []
            
            if current_batch:
                batches.append(current_batch)
        
        # Handle orphan files
        orphan_files = []
        for group in file_groups:
            for f in group.files:
                if f.path not in processed_files:
                    orphan_files.append({
                        "file": f,
                        "priority": group.priority,
                        "has_relationships": False,
                        "relationship_strength": 0.0,
                        "related_in_batch": []
                    })
                    processed_files.add(f.path)
        
        if orphan_files:
            orphan_files_sorted = sorted(
                orphan_files,
                key=lambda x: (priority_order.index(x['priority']), x['file'].path)
            )
            for i in range(0, len(orphan_files_sorted), max_batch_size):
                batches.append(orphan_files_sorted[i:i + max_batch_size])
        
        batches = self._merge_small_batches(batches, min_batch_size, max_batch_size)
        
        logger.info(f"Smart batching created {len(batches)} batches from {len(self.nodes)} files")
        for i, batch in enumerate(batches):
            paths = [b['file'].path for b in batch]
            rel_count = sum(1 for b in batch if b.get('has_relationships'))
            logger.debug(f"Batch {i+1}: {len(batch)} files ({rel_count} with relationships): {paths}")
        
        return batches
    
    def _merge_small_batches(
        self, 
        batches: List[List[Dict[str, Any]]], 
        min_size: int, 
        max_size: int
    ) -> List[List[Dict[str, Any]]]:
        """Merge small batches if they have the same priority."""
        if not batches:
            return batches
        
        priority_batches: Dict[str, List[List[Dict[str, Any]]]] = defaultdict(list)
        for batch in batches:
            if not batch:
                continue
            priorities = [b['priority'] for b in batch]
            dominant = max(set(priorities), key=priorities.count)
            priority_batches[dominant].append(batch)
        
        merged = []
        for priority, p_batches in priority_batches.items():
            current_merged = []
            for batch in p_batches:
                if len(current_merged) + len(batch) <= max_size:
                    current_merged.extend(batch)
                else:
                    if current_merged:
                        merged.append(current_merged)
                    current_merged = batch[:]
            if current_merged:
                merged.append(current_merged)
        
        return merged
    
    def get_relationship_summary(self) -> Dict[str, Any]:
        """Get a summary of discovered relationships."""
        relationship_types = defaultdict(int)
        for rel in self.relationships:
            relationship_types[rel.relationship_type] += 1
        
        return {
            "total_files": len(self.nodes),
            "total_relationships": len(self.relationships),
            "relationship_types": dict(relationship_types),
            "files_with_relationships": sum(
                1 for node in self.nodes.values() 
                if len(node.related_files) > 0
            ),
            "avg_relationships_per_file": (
                len(self.relationships) * 2 / len(self.nodes)
                if self.nodes else 0
            )
        }


def create_smart_batches(
    file_groups: List[Any], 
    workspace: str,
    project: str,
    branches: List[str],
    rag_client: Optional["RAGClient"] = None,
    max_batch_size: int = 7,
    enrichment_data: Any = None
) -> List[List[Dict[str, Any]]]:
    """
    Convenience function to create smart batches from file groups.
    
    Args:
        file_groups: List of FileGroup objects with files
        workspace: Repository workspace/owner  
        project: Repository slug
        branches: Branch names for context
        rag_client: Optional RAG client for relationship discovery
        max_batch_size: Maximum files per batch
        enrichment_data: Optional PrEnrichmentDataDto with pre-computed relationships from Java
    """
    builder = DependencyGraphBuilder(rag_client=rag_client)
    return builder.get_smart_batches(
        file_groups, 
        workspace, 
        project, 
        branches,
        max_batch_size,
        enrichment_data=enrichment_data
    )
