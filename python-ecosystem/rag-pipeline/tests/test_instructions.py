"""
Unit tests for rag_pipeline.models.instructions — InstructionType + format_query.
"""
import pytest

from rag_pipeline.models.instructions import (
    InstructionType,
    INSTRUCTIONS,
    format_query,
)


class TestInstructionType:

    def test_all_values_are_strings(self):
        for it in InstructionType:
            assert isinstance(it.value, str)

    def test_all_types_have_instructions(self):
        for it in InstructionType:
            assert it in INSTRUCTIONS, f"Missing instruction for {it}"
            assert len(INSTRUCTIONS[it]) > 20, f"Instruction too short for {it}"

    def test_expected_members(self):
        names = {it.name for it in InstructionType}
        assert "DEPENDENCY" in names
        assert "LOGIC" in names
        assert "IMPACT" in names
        assert "GENERAL" in names
        assert "DUPLICATION" in names


class TestFormatQuery:

    def test_with_instructions_supported(self):
        result = format_query("find user service", InstructionType.GENERAL, supports_instructions=True)
        assert "Instruct:" in result
        assert "Query: find user service" in result

    def test_without_instructions_returns_raw(self):
        result = format_query("find user service", InstructionType.GENERAL, supports_instructions=False)
        assert result == "find user service"
        assert "Instruct:" not in result

    def test_each_instruction_type_produces_different_prefix(self):
        results = set()
        for it in InstructionType:
            result = format_query("test", it, supports_instructions=True)
            results.add(result)
        # Each instruction type should produce a unique formatted query
        assert len(results) == len(InstructionType)

    def test_default_instruction_type(self):
        result = format_query("query text")
        assert "Instruct:" in result
        assert "Query: query text" in result

    def test_unknown_instruction_falls_back_to_general(self):
        # format_query uses dict.get with GENERAL as fallback
        result = format_query("test", InstructionType.GENERAL, supports_instructions=True)
        assert "Instruct:" in result
