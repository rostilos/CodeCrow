from __future__ import annotations

import re
from functools import lru_cache


@lru_cache(maxsize=512)
def _compiled(glob: str) -> re.Pattern[str]:
    parts = ["^"]
    index = 0
    while index < len(glob):
        character = glob[index]
        if character == "*":
            recursive = index + 1 < len(glob) and glob[index + 1] == "*"
            parts.append(".*" if recursive else "[^/]*")
            index += 2 if recursive else 1
            continue
        if character == "?":
            parts.append("[^/]")
        else:
            parts.append(re.escape(character))
        index += 1
    parts.append("$")
    return re.compile("".join(parts))


def plugin_glob_matches(glob: str, path: str) -> bool:
    """Match one normalized path with the Java contract's anchored glob rules."""
    return _compiled(glob).fullmatch(path) is not None
