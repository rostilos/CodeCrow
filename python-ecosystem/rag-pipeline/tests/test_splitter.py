"""Focused coverage for the bounded AST source splitter."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from codecrow_plugins import SyntaxContribution

from rag_pipeline.core.documents import Document, TextNode
from rag_pipeline.core.splitter import (
    ASTChunk,
    ASTCodeSplitter,
    CapturedNode,
    ChunkMetadata,
    ContentType,
    MetadataExtractor,
    QueryMatch,
    compute_file_hash,
    generate_deterministic_id,
)


def test_chunk_identity_and_file_hash_are_deterministic():
    assert generate_deterministic_id("src/a.py", "value = 1", 0) == (
        generate_deterministic_id("src/a.py", "value = 1", 0)
    )
    assert generate_deterministic_id("src/a.py", "value = 1", 0) != (
        generate_deterministic_id("src/a.py", "value = 1", 1)
    )
    assert compute_file_hash("value = 1") == compute_file_hash("value = 1")
    assert compute_file_hash("value = 1") != compute_file_hash("value = 2")


def test_default_splitter_restores_bounded_chunk_configuration():
    splitter = ASTCodeSplitter()

    assert splitter.max_chunk_size == 8000
    assert splitter.min_chunk_size == 100
    assert splitter.chunk_overlap == 200
    assert splitter.parser_threshold == 3
    assert not hasattr(splitter, "embed_model")


def test_regex_metadata_keeps_complete_unicode_inventories_and_late_signature():
    extractor = MetadataExtractor()
    expected_names = [f"方法_{index:03d}_界" for index in range(45)]
    source = "\n".join(f"def {name}(): pass" for name in expected_names)

    assert extractor.extract_names_from_content(source, "python") == expected_names

    expected_imports = [
        f"org.example{index:03d}.类型_{index:03d}_界"
        for index in range(75)
    ]
    import_source = "\n".join(
        [*(f"import {name};" for name in expected_imports),
         f"import {expected_imports[10]};"]
    )
    assert extractor.extract_inheritance(import_source, "java")["imports"] == (
        expected_imports
    )

    parameters = [f"参数_{index:02d}_界" for index in range(12)]
    signature_source = "\n".join([
        *(f"# header line {index}" for index in range(25)),
        "def late_signature(",
        *(f"    {parameter}," for parameter in parameters),
        "):",
        "    pass",
    ])
    signature = extractor.extract_signature(signature_source, "python")
    assert signature is not None
    assert signature.startswith("def late_signature(")
    assert parameters[-1] in signature
    assert signature.endswith(":")


def _rich_unicode_tree():
    source = bytearray()

    def text_node(node_type: str, value: str):
        start = len(source)
        source.extend(value.encode("utf-8"))
        end = len(source)
        source.extend(b"\n")
        return SimpleNamespace(
            type=node_type,
            start_byte=start,
            end_byte=end,
            children=[],
        )

    def named_node(node_type: str, value: str):
        identifier = text_node("identifier", value)
        return SimpleNamespace(
            type=node_type,
            start_byte=identifier.start_byte,
            end_byte=identifier.end_byte,
            children=[identifier],
        )

    expected = {
        "methods": [f"method_{index:03d}_界" for index in range(65)],
        "properties": [f"property_{index:03d}_界" for index in range(65)],
        "parameters": [f"parameter_{index:03d}_界" for index in range(45)],
        "decorators": [f"decorator_{index:03d}_界" for index in range(35)],
        "calls": [f"call_{index:03d}_界" for index in range(90)],
        "referenced_types": [f"Type_{index:03d}_界" for index in range(65)],
        "variables": [f"property_{index:03d}_界" for index in range(65)],
        "type_parameters": [f"T_{index:03d}_界" for index in range(35)],
    }
    children = [
        *(named_node("function_definition", value) for value in expected["methods"]),
        *(named_node("assignment", value) for value in expected["properties"]),
        *(named_node("parameter", value) for value in expected["parameters"]),
        *(text_node("decorator", f"@{value}") for value in expected["decorators"]),
        *(named_node("call_expression", value) for value in expected["calls"]),
        *(text_node("type_identifier", value) for value in expected["referenced_types"]),
        *(text_node("type_parameter", value) for value in expected["type_parameters"]),
        # Repeated captures must not duplicate semantic records.
        named_node("function_definition", expected["methods"][0]),
        text_node("decorator", f"@{expected['decorators'][0]}"),
    ]
    deep_call_name = "deep_call_界"
    deep_node = named_node("call_expression", deep_call_name)
    for _ in range(25):
        deep_node = SimpleNamespace(
            type="block",
            start_byte=deep_node.start_byte,
            end_byte=deep_node.end_byte,
            children=[deep_node],
        )
    children.append(deep_node)
    expected["calls"].append(deep_call_name)
    root = SimpleNamespace(
        type="module",
        start_byte=0,
        end_byte=len(source),
        children=children,
    )
    return source.decode("utf-8"), root, expected


def test_rich_ast_metadata_is_bounded_in_stable_order_with_diagnostics():
    source, root, expected = _rich_unicode_tree()
    splitter = ASTCodeSplitter()
    via_capture = ASTChunk(
        content=source,
        content_type=ContentType.FUNCTIONS_CLASSES,
        language="python",
        path="src/完整.py",
        symbol_names=["Container_界"],
    )
    via_node = ASTChunk(
        content=source,
        content_type=ContentType.FUNCTIONS_CLASSES,
        language="python",
        path="src/完整.py",
        symbol_names=["Container_界"],
    )

    splitter._extract_rich_ast_details(
        via_capture,
        SimpleNamespace(root_node=root),
        root,
        "python",
    )
    splitter._extract_rich_details_from_node(
        via_node,
        root,
        source.encode("utf-8"),
        "python",
    )

    for field_name, values in expected.items():
        limit = splitter.METADATA_LIST_LIMITS[field_name]
        assert getattr(via_capture, field_name) == values[:limit]
        assert getattr(via_node, field_name) == values[:limit]
        assert f"{field_name}_limit" in via_capture.metadata_partial_reasons
        assert f"{field_name}_limit" in via_node.metadata_partial_reasons
    assert "ast_depth_limit" in via_capture.metadata_partial_reasons
    assert "ast_depth_limit" in via_node.metadata_partial_reasons
    metadata = splitter._build_metadata(via_capture, {}, 0, 1)
    for field_name, values in expected.items():
        assert metadata[field_name] == values[
            :splitter.METADATA_LIST_LIMITS[field_name]
        ]
    assert metadata["structural_metadata_complete"] is False
    assert "ast_depth_limit" in metadata["structural_metadata_partial_reasons"]


def test_query_relationships_and_parent_members_use_inventory_caps():
    splitter = ASTCodeSplitter()
    owner = ASTChunk(
        content="class Owner_界: pass",
        content_type=ContentType.FUNCTIONS_CLASSES,
        language="python",
        path="src/完整.py",
        symbol_names=["Owner_界"],
        node_type="class",
    )
    methods = [f"method_{index:03d}_界" for index in range(70)]
    properties = [f"field_{index:03d}_界" for index in range(70)]
    ranges = [(0, 10_000, owner)]
    for index, name in enumerate(methods, 1):
        child = ASTChunk(
            content=name,
            content_type=ContentType.FUNCTIONS_CLASSES,
            language="python",
            path=owner.path,
            symbol_names=[name],
            node_type="method",
        )
        ranges.append((index * 10, index * 10 + 5, child))
    for index, name in enumerate(properties, 101):
        child = ASTChunk(
            content=name,
            content_type=ContentType.FUNCTIONS_CLASSES,
            language="python",
            path=owner.path,
            symbol_names=[name],
            node_type="field",
        )
        ranges.append((index * 10, index * 10 + 5, child))

    splitter._attach_query_parent_context(ranges)

    assert owner.methods == methods[:50]
    assert owner.properties == properties[:50]
    assert owner.metadata_partial_reasons == [
        "methods_limit",
        "properties_limit",
    ]

    def captured(capture_name: str, value: str, offset: int) -> CapturedNode:
        return CapturedNode(
            name=capture_name,
            text=value,
            start_byte=offset,
            end_byte=offset + 1,
            start_point=(0, offset),
            end_point=(0, offset + 1),
            node_type="identifier",
        )

    relationship_owner = ASTChunk(
        content="def relationship_owner(): pass",
        content_type=ContentType.FUNCTIONS_CLASSES,
        language="python",
        path=owner.path,
    )
    matches = []
    expected_calls = [f"dependency_{index:03d}_界" for index in range(90)]
    expected_parameters = [f"argument_{index:03d}_界" for index in range(45)]
    expected_fields = [f"field_{index:03d}_界" for index in range(65)]
    expected_variables = [f"variable_{index:03d}_界" for index in range(65)]
    expected_types = [f"Type_{index:03d}_界" for index in range(65)]
    offset = 1
    for pattern_name, capture_name, values in (
        ("call", "call.name", expected_calls),
        ("parameter", "parameter.name", expected_parameters),
        ("field", "field.name", expected_fields),
        ("variable", "variable.name", expected_variables),
        ("type_reference", "type_reference.name", expected_types),
    ):
        for value in values:
            main = captured(pattern_name, value, offset)
            matches.append(QueryMatch(pattern_name, {
                pattern_name: main,
                capture_name: captured(capture_name, value, offset),
            }))
            offset += 1
    # Exercise stable de-duplication as well as former list boundaries.
    duplicate = captured("call", expected_calls[0], offset)
    matches.append(QueryMatch("call", {
        "call": duplicate,
        "call.name": captured("call.name", expected_calls[0], offset),
    }))

    splitter._attach_query_relationship_metadata(
        matches,
        [(0, 10_000, relationship_owner)],
    )

    assert relationship_owner.calls == expected_calls[:80]
    assert relationship_owner.parameters == expected_parameters[:30]
    assert relationship_owner.properties == expected_fields[:50]
    assert relationship_owner.variables == expected_variables[:50]
    assert relationship_owner.referenced_types == expected_types[:50]
    assert set(relationship_owner.metadata_partial_reasons) == {
        "calls_limit",
        "parameters_limit",
        "properties_limit",
        "variables_limit",
        "referenced_types_limit",
    }


def test_symbol_inventory_is_bounded_without_changing_primary_path_identity():
    symbols = [f"symbol_{index:03d}_界" for index in range(75)]
    chunk = ASTChunk(
        content="class Container_界: pass",
        content_type=ContentType.FUNCTIONS_CLASSES,
        language="python",
        path="src/完整.py",
        symbol_names=symbols,
        parent_context=["package_界", "Owner_界"],
    )
    metadata = ASTCodeSplitter()._build_metadata(chunk, {}, 0, 1)

    assert metadata["symbol_names"] == symbols[:30]
    assert metadata["primary_name"] == symbols[0]
    assert metadata["full_path"] == f"package_界.Owner_界.{symbols[0]}"
    assert metadata["structural_metadata_complete"] is False
    assert metadata["structural_metadata_partial_reasons"] == [
        "symbol_names_limit"
    ]


def test_unknown_source_is_split_into_bounded_raw_chunks():
    source = "\n".join(
        f"opaque line {index:03d} " + "x" * 30
        for index in range(30)
    )
    splitter = ASTCodeSplitter(
        max_chunk_size=160,
        min_chunk_size=1,
        chunk_overlap=20,
    )

    nodes = splitter.split_documents([
        Document(source, {"path": "assets/data.unknown", "language": "text"})
    ])

    assert len(nodes) > 1
    assert all(0 < len(node.text) <= 160 for node in nodes)
    assert all(
        node.metadata["content_type"] == ContentType.FALLBACK.value
        for node in nodes
    )
    assert all("structural_file" not in node.metadata for node in nodes)
    assert all("plugin_graph_facts" not in node.metadata for node in nodes)
    assert "".join(node.text for node in nodes) == source


def test_fallback_preserves_a_small_trailing_fragment_exactly():
    source = ("A" * 20) + "\n" + "z"
    splitter = ASTCodeSplitter(
        max_chunk_size=21,
        min_chunk_size=100,
        chunk_overlap=10,
    )

    nodes = splitter._split_fallback(Document(
        source,
        {"path": "assets/tail.unknown", "language": "text"},
    ))

    assert [node.text for node in nodes] == [("A" * 20) + "\n", "z"]
    assert "".join(node.text for node in nodes).encode("utf-8") == source.encode(
        "utf-8"
    )


def test_fallback_losslessly_bounds_a_fragment_above_thirty_thousand_chars():
    source = "X" * 35_001
    splitter = ASTCodeSplitter(
        max_chunk_size=50_000,
        min_chunk_size=1,
        chunk_overlap=200,
    )

    nodes = splitter._split_fallback(Document(
        source,
        {"path": "assets/large.unknown", "language": "text"},
    ))

    assert [len(node.text) for node in nodes] == [8_000, 8_000, 8_000, 8_000, 3_001]
    assert all(
        len(node.text) <= ASTCodeSplitter.DEFAULT_MAX_CHUNK_SIZE
        for node in nodes
    )
    assert "".join(node.text for node in nodes) == source


def test_fallback_hard_splits_only_an_indivisible_atom_without_byte_loss():
    source = "界" * 25_001
    splitter = ASTCodeSplitter(
        max_chunk_size=10_000,
        min_chunk_size=1,
        chunk_overlap=200,
    )

    nodes = splitter._split_fallback(Document(
        source,
        {"path": "assets/atom.unknown", "language": "text"},
    ))

    assert [len(node.text) for node in nodes] == [8_000, 8_000, 8_000, 1_001]
    assert all(
        len(node.text) <= ASTCodeSplitter.DEFAULT_MAX_CHUNK_SIZE
        for node in nodes
    )
    assert b"".join(node.text.encode("utf-8") for node in nodes) == source.encode(
        "utf-8"
    )


def test_oversized_ast_unit_is_fragmented_without_copying_global_details():
    splitter = ASTCodeSplitter(
        max_chunk_size=120,
        min_chunk_size=1,
        chunk_overlap=10,
    )
    chunk = ASTChunk(
        content="\n".join(
            f"    value_{index} = dependency_{index}()"
            for index in range(20)
        ),
        content_type=ContentType.FUNCTIONS_CLASSES,
        language="python",
        path="src/service.py",
        # Only the primary owning identity is repeated on each fragment; the
        # intact semantic unit path retains the complete symbol inventory.
        symbol_names=["run", *[f"nested_{index}_界" for index in range(70)]],
        calls=[f"dependency_{index}" for index in range(20)],
        start_line=1,
        end_line=20,
        node_type="function",
    )

    nodes = splitter._process_chunks(
        [chunk],
        Document(chunk.content, {"path": chunk.path}),
        None,
        chunk.path,
    )

    assert len(nodes) > 1
    assert all(len(node.text) <= 120 for node in nodes)
    assert all(node.metadata["is_fragment"] is True for node in nodes)
    assert all(
        node.metadata["content_type"] == ContentType.OVERSIZED_SPLIT.value
        for node in nodes
    )
    assert all(node.metadata["primary_name"] == "run" for node in nodes)
    assert all(node.metadata["symbol_names"] == ["run"] for node in nodes)
    assert all(node.metadata["fragment_of"] == "run" for node in nodes)
    assert len({node.id_ for node in nodes}) == len(nodes)
    assert [node.metadata["sub_chunk_index"] for node in nodes] == list(
        range(len(nodes))
    )
    assert all(
        node.metadata["total_sub_chunks"] == len(nodes) for node in nodes
    )
    assert len({node.metadata["parent_chunk_id"] for node in nodes}) == 1
    assert all("calls" not in node.metadata for node in nodes)

    previous_offset = -1
    for node in nodes:
        fragment_offset = chunk.content.find(node.text, previous_offset + 1)
        assert fragment_offset >= 0
        previous_offset = fragment_offset
        expected_start_line = (
            chunk.start_line
            + chunk.content[:fragment_offset].count("\n")
        )
        assert node.metadata["start_line"] == expected_start_line
        assert node.metadata["end_line"] == (
            expected_start_line + node.text.count("\n")
        )
        assert chunk.start_line <= node.metadata["start_line"]
        assert node.metadata["end_line"] <= chunk.end_line


def test_complete_docstring_is_preserved_in_both_metadata_paths():
    docstring = "contract evidence " + ("\U0001f9ea" * 2_000)
    splitter = ASTCodeSplitter()
    ast_chunk = ASTChunk(
        content="def run():\n    pass\n",
        content_type=ContentType.FUNCTIONS_CLASSES,
        language="python",
        path="src/service.py",
        docstring=docstring,
    )
    extracted = ChunkMetadata(
        content_type=ContentType.FUNCTIONS_CLASSES,
        language="python",
        path="src/service.py",
        docstring=docstring,
    )

    assert splitter._build_metadata(ast_chunk, {}, 0, 1)["docstring"] == docstring
    assert MetadataExtractor().build_metadata_dict(extracted, {})["docstring"] == docstring


def test_plugin_runtime_remains_the_syntax_selection_boundary():
    syntax = SyntaxContribution(
        plugin_id="python",
        language_id="python",
        grammar_module="tree_sitter_python",
        grammar_factory="language",
        query_resource="python/resources/rag-chunks.scm",
        builtin_tags=True,
    )
    capabilities = SimpleNamespace(file_plugins={"src/service.py": ("python",)})
    runtime = MagicMock()
    runtime.syntax_contribution.return_value = (syntax, ())
    splitter = ASTCodeSplitter(plugin_runtime=runtime)
    splitter._parser = MagicMock()
    splitter._parser.is_available.return_value = False

    nodes = splitter.split_documents(
        [Document(
            "def run():\n    return True\n",
            {"path": "src/service.py", "language": "python"},
        )],
        capabilities=capabilities,
    )

    runtime.syntax_contribution.assert_called_once_with(
        "src/service.py", capabilities
    )
    assert nodes
    assert all(node.metadata["plugin_syntax"] == {
        "plugin": "python",
        "language": "python",
    } for node in nodes)


def test_resilient_splitter_quarantines_only_the_failing_file():
    splitter = ASTCodeSplitter()

    def split_one(documents, capabilities=None):
        document = documents[0]
        if document.metadata["path"] == "broken.py":
            raise RuntimeError("parser crashed")
        return [TextNode(document.text, dict(document.metadata))]

    splitter.split_documents = MagicMock(side_effect=split_one)
    nodes, skipped = splitter.split_documents_resilient([
        Document("value = 1", {"path": "ok.py"}),
        Document("broken", {"path": "broken.py"}),
        Document("value = 2", {"path": "later.py"}),
    ])

    assert [node.metadata["path"] for node in nodes] == ["ok.py", "later.py"]
    assert skipped == ("broken.py",)


def test_python_ast_chunks_keep_source_and_structural_metadata():
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_python")
    source = (
        "class Service:\n"
        "    def run(self, value):\n"
        "        return validate(value)\n"
    )
    splitter = ASTCodeSplitter(max_chunk_size=8000, min_chunk_size=1)

    nodes = splitter.split_documents([
        Document(source, {"path": "src/service.py", "language": "python"})
    ])

    assert nodes
    assert all(len(node.text) <= 8000 for node in nodes)
    assert any("class Service" in node.text for node in nodes)
    assert {"Service", "run"}.intersection({
        node.metadata.get("primary_name") for node in nodes
    })
    assert any("validate" in node.metadata.get("calls", []) for node in nodes)
    assert all("structural_record_type" not in node.metadata for node in nodes)
