"""JSONC decoding shared by configuration plugins."""
import json
import re


def load_jsonc(content: str):
    # Keep strings and line positions intact while removing comments and trailing commas.
    string = r'"(?:\\.|[^"\\])*"'
    content = re.sub(string + r'|//[^\r\n]*|/\*[\s\S]*?\*/',
        lambda match: match[0] if match[0].startswith('"') else re.sub(r'[^\r\n]', ' ', match[0]),
        content.lstrip('\ufeff'))
    content = re.sub(string + r'|,(?=\s*[}\]])',
        lambda match: match[0] if match[0].startswith('"') else ' ', content)
    return json.loads(content)
