from __future__ import annotations

import ast
from dataclasses import dataclass

from codecrow_plugins import (
    CandidateClaim, EvidenceRequest, FileArtifact, GraphFact, PluginDescriptor,
    PluginDiagnostic, PluginOutcome, ReviewContribution, ValidationDecision,
    ValidationResult,
)


_EXTENSIONS = (".py", ".pyi", ".pyw")
_ROUTE_METHODS = {"delete", "get", "head", "options", "patch", "post", "put", "websocket"}


def _name(node: ast.AST | None) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Call):
        return _name(node.func)
    if isinstance(node, ast.Subscript):
        return _name(node.value)
    return ""


def _literal(node: ast.AST | None) -> str:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else ""


def _keyword(call: ast.Call, name: str) -> ast.AST | None:
    return next((keyword.value for keyword in call.keywords if keyword.arg == name), None)


def _callable_dependencies(function: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[tuple[str, int], ...]:
    dependencies: set[tuple[str, int]] = set()
    candidates = [*function.args.defaults, *function.args.kw_defaults]
    for candidate in candidates:
        if isinstance(candidate, ast.Call) and _name(candidate.func).endswith("Depends"):
            dependency = _name(candidate.args[0]) if candidate.args else "dependency"
            dependencies.add((dependency, candidate.lineno))
    for argument in (*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs):
        annotation = argument.annotation
        if isinstance(annotation, ast.Subscript) and _name(annotation.value).endswith("Annotated"):
            for item in ast.walk(annotation.slice):
                if isinstance(item, ast.Call) and _name(item.func).endswith("Depends"):
                    dependency = _name(item.args[0]) if item.args else "dependency"
                    dependencies.add((dependency, item.lineno))
    return tuple(sorted(dependencies))


@dataclass(frozen=True)
class FastApiPlugin:
    descriptor: PluginDescriptor

    def index_file(self, artifact: FileArtifact):
        if artifact.deleted or not artifact.path.casefold().endswith(_EXTENSIONS):
            return PluginOutcome.abstained()
        try:
            tree = ast.parse(artifact.content, filename=artifact.path, type_comments=True)
        except SyntaxError as exception:
            return PluginOutcome.failed(PluginDiagnostic(
                "fastapi-python-parse-failed",
                f"SyntaxError at line {exception.lineno}: {exception.msg}", self.descriptor.id,
            ))

        module = artifact.path.removesuffix(".py").replace("/", ".")
        app_names: set[str] = set()
        router_names: set[str] = set()
        facts: set[GraphFact] = set()

        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                if not isinstance(value, ast.Call):
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names = {_name(target) for target in targets if _name(target)}
                constructor = _name(value.func).rsplit(".", 1)[-1]
                if constructor == "FastAPI":
                    app_names.update(names)
                    for app_name in names:
                        facts.add(GraphFact("fastapi-application", module, "declares", app_name, artifact.path, node.lineno))
                        lifespan = _name(_keyword(value, "lifespan"))
                        if lifespan:
                            facts.add(GraphFact("fastapi-lifespan", app_name, "uses", lifespan, artifact.path, node.lineno))
                elif constructor == "APIRouter":
                    router_names.update(names)
                    for router_name in names:
                        prefix = _literal(_keyword(value, "prefix"))
                        facts.add(GraphFact(
                            "fastapi-router", module, "declares", router_name, artifact.path, node.lineno,
                            (("prefix", prefix),) if prefix else (),
                        ))

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                callable_name = f"{module}.{node.name}"
                for decorator in node.decorator_list:
                    if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                        continue
                    method = decorator.func.attr.casefold()
                    owner = _name(decorator.func.value)
                    if method in _ROUTE_METHODS and (owner in router_names or owner in app_names):
                        route = _literal(decorator.args[0]) if decorator.args else ""
                        facts.add(GraphFact(
                            "fastapi-route", callable_name, "handles", f"{method.upper()} {route or '/'}",
                            artifact.path, decorator.lineno, (("router", owner),),
                        ))
                    elif method == "exception_handler" and owner in app_names:
                        exception = _name(decorator.args[0]) if decorator.args else "Exception"
                        facts.add(GraphFact("fastapi-exception-handler", owner, "handles", exception, artifact.path, decorator.lineno))
                for dependency, line in _callable_dependencies(node):
                    facts.add(GraphFact("fastapi-dependency", callable_name, "depends-on", dependency, artifact.path, line))
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                owner = _name(node.func.value)
                operation = node.func.attr
                if operation == "include_router" and owner in app_names:
                    router = _name(node.args[0]) if node.args else "router"
                    prefix = _literal(_keyword(node, "prefix"))
                    facts.add(GraphFact(
                        "fastapi-router", owner, "includes", router, artifact.path, node.lineno,
                        (("prefix", prefix),) if prefix else (),
                    ))
                elif operation == "add_middleware" and owner in app_names:
                    middleware = _name(node.args[0]) if node.args else "middleware"
                    facts.add(GraphFact("fastapi-middleware", owner, "uses", middleware, artifact.path, node.lineno))

        if not facts:
            return PluginOutcome.abstained()
        return PluginOutcome.handled(tuple(sorted(facts)))

    def review(self, paths: tuple[str, ...]):
        selected = tuple(sorted(path for path in paths if path.casefold().endswith(_EXTENSIONS)))
        if not selected:
            return PluginOutcome.abstained()
        return PluginOutcome.handled(ReviewContribution(
            rules=(
                "Resolve FastAPI routes through application/router inclusion, prefixes, decorators, and dependency facts before judging endpoint behavior.",
                "Treat lifespan, middleware, exception handlers, and Depends wiring as execution context for affected FastAPI callables.",
            ),
            evidence_requests=tuple(EvidenceRequest(
                "fastapi-component", path,
                "exact FastAPI application, router, route, dependency, middleware, lifespan, and exception-handler facts",
            ) for path in selected),
        ))

    def validate(self, claim: CandidateClaim):
        owns_claim = claim.claim_kind == "fastapi-component" or (
            not claim.claim_kind and claim.category.startswith("fastapi-")
        )
        if not owns_claim or not claim.path.casefold().endswith(_EXTENSIONS):
            return PluginOutcome.abstained()
        if not any(fact.path == claim.path and fact.kind.startswith("fastapi-") for fact in claim.evidence):
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.INSUFFICIENT_EVIDENCE, "fastapi-missing-architecture-evidence",
                "The candidate has no exact FastAPI architecture evidence from its reported path.",
            ))
        return PluginOutcome.handled(ValidationResult(
            ValidationDecision.PASS, "fastapi-architecture-evidence-present",
            "Exact FastAPI architecture evidence is present.",
        ))


def create_plugin(descriptor: PluginDescriptor) -> FastApiPlugin:
    return FastApiPlugin(descriptor)
