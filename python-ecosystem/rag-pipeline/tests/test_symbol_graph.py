from rag_pipeline.api.models import (
    ParsedFileMetadata,
    ParsedRelationship,
    ParsedSymbol,
)
from rag_pipeline.api.symbol_graph import resolve_parsed_repository_graph


def _symbol(path: str, name: str, qualified_name: str, suffix: str) -> ParsedSymbol:
    return ParsedSymbol(
        symbol_id=(suffix * 64)[:64],
        path=path,
        name=name,
        qualified_name=qualified_name,
        kind="class_declaration",
        start_line=1,
        end_line=5,
    )


def _relationship(
    source: ParsedSymbol,
    target_name: str,
    relationship_type: str = "extends",
) -> ParsedRelationship:
    return ParsedRelationship(
        relationship_id="f" * 64,
        source_symbol_id=source.symbol_id,
        source_name=source.qualified_name,
        target_name=target_name,
        relationship_type=relationship_type,
        source_line=source.start_line,
    )


def test_resolves_unique_qualified_symbol_with_exact_path():
    child = _symbol("src/Child.java", "Child", "example.Child", "a")
    base = _symbol("src/Base.java", "Base", "example.Base", "b")
    files = [
        ParsedFileMetadata(
            path=child.path,
            namespace="example",
            symbols=[child],
            relationships=[_relationship(child, "example.Base")],
            ast_supported=True,
        ),
        ParsedFileMetadata(
            path=base.path,
            namespace="example",
            symbols=[base],
            ast_supported=True,
        ),
    ]

    resolved_files, graph = resolve_parsed_repository_graph(files)

    edge = resolved_files[0].relationships[0]
    assert edge.resolution == "resolved"
    assert edge.target_symbol_id == base.symbol_id
    assert edge.target_path == "src/Base.java"
    assert edge.confidence == 1.0
    assert graph.resolved_count == 1
    assert graph.unresolved_count == 0


def test_simple_name_collision_is_ambiguous_instead_of_guessed():
    caller = _symbol("src/Caller.py", "Caller", "app.Caller", "a")
    first = _symbol("src/a/Helper.py", "Helper", "a.Helper", "b")
    second = _symbol("src/b/Helper.py", "Helper", "b.Helper", "c")
    files = [
        ParsedFileMetadata(
            path=caller.path,
            symbols=[caller],
            relationships=[_relationship(caller, "Helper", "calls")],
            ast_supported=True,
        ),
        ParsedFileMetadata(path=first.path, symbols=[first], ast_supported=True),
        ParsedFileMetadata(path=second.path, symbols=[second], ast_supported=True),
    ]

    resolved_files, graph = resolve_parsed_repository_graph(files)

    edge = resolved_files[0].relationships[0]
    assert edge.resolution == "ambiguous"
    assert edge.target_symbol_id is None
    assert graph.ambiguous_count == 1
    assert any(gap.startswith("ambiguous:") for gap in graph.resolution_gaps)


def test_unresolved_external_dependency_and_parser_degradation_are_explicit():
    source = _symbol("src/App.ts", "App", "App", "a")
    files = [ParsedFileMetadata(
        path=source.path,
        symbols=[source],
        relationships=[_relationship(source, "external.library.Type", "imports")],
        ast_supported=False,
        degraded_reason="ast_query_unavailable",
    )]

    resolved_files, graph = resolve_parsed_repository_graph(files)

    assert resolved_files[0].relationships[0].resolution == "unresolved"
    assert graph.unresolved_count == 1
    assert any(gap.startswith("unresolved:") for gap in graph.resolution_gaps)
    assert any(gap.startswith("degraded_parser:") for gap in graph.resolution_gaps)
