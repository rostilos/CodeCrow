
import sys
import os
import importlib.util

# Add src to path
src_path = os.path.join(os.path.dirname(__file__), 'python-ecosystem/rag-pipeline/src')
instructions_path = os.path.join(src_path, 'rag_pipeline/models/instructions.py')

spec = importlib.util.spec_from_file_location("instructions", instructions_path)
instructions = importlib.util.module_from_spec(spec)
sys.modules["instructions"] = instructions
spec.loader.exec_module(instructions)

from instructions import InstructionType, format_query, INSTRUCTIONS

def test_instruction_formatting():
    print("Testing Instruction Formatting...")
    
    # Test Dependency
    query = "foo()"
    formatted = format_query(query, InstructionType.DEPENDENCY)
    expected_instr = INSTRUCTIONS[InstructionType.DEPENDENCY]
    expected = f"Instruct: {expected_instr}\nQuery: {query}"
    
    if formatted == expected:
        print("✅ Dependency instruction formatted correctly")
    else:
        print(f"❌ Dependency instruction mismatch.\nExpected:\n{expected}\nGot:\n{formatted}")
        return False

    # Test Logic
    query = "User logic"
    formatted = format_query(query, InstructionType.LOGIC)
    expected_instr = INSTRUCTIONS[InstructionType.LOGIC]
    expected = f"Instruct: {expected_instr}\nQuery: {query}"
    
    if formatted == expected:
        print("✅ Logic instruction formatted correctly")
    else:
        print("❌ Logic instruction mismatch")
        return False

    return True

if __name__ == "__main__":
    if test_instruction_formatting():
        sys.exit(0)
    else:
        sys.exit(1)
