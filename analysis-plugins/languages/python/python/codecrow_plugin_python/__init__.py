from __future__ import annotations

import ast
from dataclasses import dataclass

from codecrow_plugins import (
    CandidateClaim, EvidenceRequest, FileArtifact, GraphFact, PluginDescriptor,
    ImportGraphSession, PluginDiagnostic, PluginOutcome, ReviewContribution,
    SyntaxContribution, ValidationDecision, ValidationResult,
)

from .repository import (
    PYTHON_EXTENSIONS,
    parse_import_record,
    resolve_import,
)

_EXTENSIONS = PYTHON_EXTENSIONS
_SNAPSHOT_KIND = "python-import-graph"


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Subscript):
        return _name(node.value)
    if isinstance(node, ast.Call):
        return _name(node.func)
    try:
        return ast.unparse(node)
    except Exception:
        return ""


@dataclass(frozen=True)
class PythonPlugin:
    descriptor: PluginDescriptor

    def syntax(self):
        return PluginOutcome.handled(SyntaxContribution(
            plugin_id=self.descriptor.id,
            language_id="python",
            grammar_module="tree_sitter_python",
            grammar_factory="language",
            query_resource="python/resources/rag-chunks.scm",
            builtin_tags=True,
        ))

    def start_repository_analysis(self, revision: str):
        return PluginOutcome.handled(ImportGraphSession(
            plugin_id=self.descriptor.id,
            revision=revision,
            snapshot_kind=_SNAPSHOT_KIND,
            parser=parse_import_record,
            resolver=resolve_import,
        ))

    def restore_repository_analysis(self, revision: str, snapshots):
        return PluginOutcome.handled(ImportGraphSession.restore(
            plugin_id=self.descriptor.id,
            revision=revision,
            snapshot_kind=_SNAPSHOT_KIND,
            parser=parse_import_record,
            resolver=resolve_import,
            snapshots=snapshots,
        ))

    def index_file(self, artifact: FileArtifact):
        if not artifact.path.casefold().endswith(_EXTENSIONS) or artifact.deleted:
            return PluginOutcome.abstained()
        try:
            tree = ast.parse(artifact.content, filename=artifact.path, type_comments=True)
        except SyntaxError as exception:
            return PluginOutcome.failed(PluginDiagnostic(
                "python-parse-failed", f"SyntaxError at line {exception.lineno}: {exception.msg}", self.descriptor.id,
            ))
        module = artifact.path.removesuffix(".py").replace("/", ".")
        facts: set[GraphFact] = {GraphFact("python-module", artifact.path, "declares", module, artifact.path)}
        owners: list[tuple[int, int, str]] = []
        for node in ast.walk(tree):
            line = getattr(node, "lineno", 1)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    facts.add(GraphFact("python-import", module, "imports", alias.name, artifact.path, line))
            elif isinstance(node, ast.ImportFrom):
                prefix = "." * node.level + (node.module or "")
                for alias in node.names:
                    facts.add(GraphFact("python-import", module, "imports", f"{prefix}.{alias.name}".strip("."), artifact.path, line))
            elif isinstance(node, ast.ClassDef):
                qualified = f"{module}.{node.name}"
                owners.append((node.lineno, getattr(node, "end_lineno", node.lineno), qualified))
                facts.add(GraphFact("python-type", module, "declares", qualified, artifact.path, line, (("type", "class"),)))
                for base in node.bases:
                    target = _name(base)
                    if target:
                        facts.add(GraphFact("python-inheritance", qualified, "extends", target, artifact.path, line))
                for decorator in node.decorator_list:
                    target = _name(decorator)
                    if target:
                        facts.add(GraphFact("python-decorator", qualified, "decorated-by", target, artifact.path, getattr(decorator, "lineno", line)))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                owner = next((name for start, end, name in reversed(owners) if start <= line <= end), module)
                relation = "declares-async-function" if isinstance(node, ast.AsyncFunctionDef) else "declares-function"
                facts.add(GraphFact("python-callable", owner, relation, node.name, artifact.path, line))
                for decorator in node.decorator_list:
                    target = _name(decorator)
                    if target:
                        facts.add(GraphFact("python-decorator", f"{owner}.{node.name}", "decorated-by", target, artifact.path, getattr(decorator, "lineno", line)))
            elif isinstance(node, ast.Call):
                target = _name(node.func)
                if target:
                    facts.add(GraphFact("python-call", module, "calls", target, artifact.path, line))
        return PluginOutcome.handled(tuple(sorted(facts)))

    def review(self, paths: tuple[str, ...]):
        selected = tuple(sorted(path for path in paths if path.casefold().endswith(_EXTENSIONS)))
        if not selected:
            return PluginOutcome.abstained()
        return PluginOutcome.handled(ReviewContribution(
            rules=(
                "A python-pr-removed-relation is base-to-PR navigation evidence only; require changed-hunk proof of harm.",
                "For Python dynamic typing or async claims, require exact annotations, guards, await/call sites, or framework wiring evidence rather than inferring from names.",
                "Resolve Python imports, decorators, inheritance, and call targets before reporting a missing symbol or invalid contract.",
                "Treat exact python-module-resolution, python-import-binding, and python-call-resolution facts as navigation evidence; relationship presence alone is not proof of a defect.",
            ),
            evidence_requests=tuple(EvidenceRequest("python-file", path, "exact Python module, declaration, decorator, inheritance, and call facts") for path in selected),
        ))

    def validate(self, claim: CandidateClaim):
        owns_claim = claim.claim_kind == "python-file" or (
            not claim.claim_kind and claim.category.startswith("python-")
        )
        if not owns_claim or not claim.path.casefold().endswith(_EXTENSIONS):
            return PluginOutcome.abstained()
        if not any(fact.path == claim.path and fact.kind.startswith("python-") for fact in claim.evidence):
            return PluginOutcome.handled(ValidationResult(ValidationDecision.INSUFFICIENT_EVIDENCE, "python-missing-local-evidence", "The candidate has no exact Python semantic evidence from its reported path."))
        return PluginOutcome.handled(ValidationResult(ValidationDecision.PASS, "python-evidence-present", "Exact Python semantic evidence is present."))


def create_plugin(descriptor: PluginDescriptor) -> PythonPlugin:
    return PythonPlugin(descriptor)
