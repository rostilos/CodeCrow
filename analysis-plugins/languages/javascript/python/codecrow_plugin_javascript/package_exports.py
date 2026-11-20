"""Repository package identities and unconditional export navigation aliases."""
import posixpath
import re

from codecrow_plugins import ArchitecturePacket, GraphFact


def _safe_target(value):
    return (isinstance(value, str) and value.startswith("./")
            and not any(char in value for char in "\\%?#")
            and all(part.casefold() not in {"", ".", "..", "node_modules"} for part in value[2:].split("/")))


def _selected_key(entries, subpath):
    if subpath in entries and "*" not in subpath:
        return subpath
    matches = []
    for key in entries:
        if "*" not in key:
            continue
        prefix, suffix = key.split("*")
        if subpath.startswith(prefix) and subpath.endswith(suffix) and len(subpath) > len(prefix) + len(suffix):
            matches.append(key)
    return min(matches, key=lambda key: (-key.index("*"), -len(key))) if matches else None


def package_declaration_packets(plugin_id, packages, paths):
    packets = []
    for manifest_path, manifest in sorted(packages.items()):
        name = manifest.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"(?:@[A-Za-z0-9_~-][A-Za-z0-9._~-]*/)?[A-Za-z0-9_~-][A-Za-z0-9._~-]*", name):
            continue
        packets.append(ArchitecturePacket(
            plugin_id=plugin_id, kind="javascript-package", key=manifest_path,
            paths=(manifest_path,), facts=(GraphFact(
                "javascript-package", name, "declares-package", manifest_path,
                manifest_path, attributes=(("jsonPointer", "/name"),),
                related_paths=(manifest_path,),
            ),), attributes=(("resolution", "declared-package-identity"),),
        ))
        exports = manifest.get("exports")
        entries = exports if isinstance(exports, dict) and any(key.startswith(".") for key in exports) else {".": exports}
        if any(not (key == "." or key.startswith("./")) or key.count("*") > 1 for key in entries):
            continue
        directory = posixpath.dirname(manifest_path)
        prefix = directory + "/" if directory else ""
        files = {"./" + path[len(prefix):]: path for path in paths if path.startswith(prefix)}
        facts = set()
        for key, target in entries.items():
            # shortcut: conditions and fallback arrays need runtime conditions; leave their aliases unknown.
            if not _safe_target(target):
                continue
            pointer = "/exports" + ("/" + key.replace("~", "~0").replace("/", "~1")
                                    if isinstance(exports, dict) and entries is exports else "")
            candidates = []
            if "*" not in key and "*" not in target:
                if target in files:
                    candidates.append((key, files[target]))
            elif "*" in key and "*" in target:
                facts.add(GraphFact(
                    "javascript-package-export-pattern", manifest_path, "declares-file-pattern",
                    prefix + target[2:], manifest_path,
                    attributes=(("exportKey", key), ("jsonPointer", pointer), ("targetSpecifier", target)),
                ))
                parts = target.split("*")
                expression = (re.escape(parts[0]) + "(?P<subpath>.*)" + re.escape(parts[1])
                              + "".join("(?P=subpath)" + re.escape(part) for part in parts[2:]))
                for relative, path in files.items():
                    match = re.fullmatch(expression, relative)
                    if match and _safe_target(relative) and _safe_target('./' + match['subpath']):
                        candidates.append((key.replace("*", match['subpath']), path))
            for subpath, path in candidates:
                # Null/conditional exact entries and more-specific patterns still shadow broad patterns.
                if _selected_key(entries, subpath) != key:
                    continue
                if subpath != "." and not _safe_target(subpath):
                    continue
                alias = name if subpath == "." else name + subpath[1:]
                facts.add(GraphFact(
                    "javascript-package-export", alias, "declares-file-alias", path,
                    manifest_path, attributes=(("exportKey", key), ("jsonPointer", pointer),
                                               ("targetSpecifier", target)), related_paths=(path,),
                ))
        if facts:
            packets.append(ArchitecturePacket(
                plugin_id=plugin_id, kind="javascript-package-export", key=manifest_path,
                paths=tuple(sorted({manifest_path, *(fact.target for fact in facts if fact.relation == "declares-file-alias")})),
                facts=tuple(sorted(facts)), attributes=(("resolution", "declared-repository-alias"),),
            ))
    return tuple(sorted(packets))
