from __future__ import annotations

from dataclasses import dataclass
import re

from codecrow_plugins import (
    FileArtifact, GraphFact, PluginDescriptor, PluginDiagnostic, PluginOutcome,
    TreeSitterDocument,
)


def _parse(source: str) -> TreeSitterDocument:
    return TreeSitterDocument.parse(source, "tree_sitter_typescript", "language_typescript")


def _frontmatter(content: str) -> TreeSitterDocument | None:
    opening = re.match(r"\A\ufeff?\s*---", content)
    if opening is None:
        return None
    # Preserve source coordinates, including inline fences, without parsing the opening fence.
    prefix = re.sub(r"[^\r\n]", " ", content[:opening.end()])
    document = _parse(prefix + content[opening.end():])
    protected = tuple((node.start_byte, node.end_byte) for node in document.walk()
                      if node.type in {"string", "template_string", "comment", "regex"})
    offset = len(prefix.encode("utf-8"))
    for fence in re.finditer(b"---", document.source[offset:]):
        end = offset + fence.start()
        if any(start <= end < stop for start, stop in protected):
            continue
        script = _parse(document.source[:end].decode('utf-8'))
        if script.root.has_error:
            raise ValueError("Astro component script has unsupported or malformed TypeScript")
        return script
    raise ValueError("Astro component script has no recognized closing fence")


@dataclass(frozen=True)
class AstroPlugin:
    descriptor: PluginDescriptor

    def index_file(self, artifact: FileArtifact):
        if artifact.deleted or not artifact.path.casefold().endswith(".astro"):
            return PluginOutcome.abstained()
        try:
            document = _frontmatter(artifact.content)
            if document is None:
                return PluginOutcome.abstained()
            facts = set()
            for node in document.walk():
                source = None
                dynamic = False
                if node.type in {"import_statement", "export_statement"}:
                    source = node.child_by_field_name("source")
                elif node.type == "call_expression":
                    function = node.child_by_field_name("function")
                    arguments = node.child_by_field_name("arguments")
                    if function is not None and function.type == "import" and arguments is not None:
                        source = next((child for child in arguments.named_children if child.type != "comment"), None)
                        dynamic = True
                if source is None or source.type not in {"string", "template_string"}:
                    continue
                # shortcut: escaped literal parts and non-template computed arguments need JS evaluation.
                allowed = {"string_fragment", "template_substitution"} if dynamic else {"string_fragment"}
                if any(child.type not in allowed for child in source.named_children):
                    continue
                pattern = any(child.type == "template_substitution" for child in source.named_children)
                module = document.text(source)[1:-1]
                if module.strip():
                    facts.add(GraphFact(
                        "astro-import", artifact.path, "imports-pattern" if pattern else "imports", module,
                        artifact.path, document.line(node),
                        attributes=(("componentScript", "true"), ("dynamic", str(dynamic).lower())),
                    ))
            return PluginOutcome.handled(tuple(sorted(facts)))
        except Exception as exception:
            return PluginOutcome.failed(PluginDiagnostic(
                "astro-frontmatter-parse-failed", f"{type(exception).__name__}: {exception}",
                self.descriptor.id, path=artifact.path, recoverable=True,
            ))


def create_plugin(descriptor: PluginDescriptor) -> AstroPlugin:
    return AstroPlugin(descriptor)
