from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from codecrow_plugins import (
    CandidateClaim,
    EvidenceRequest,
    FileArtifact,
    GraphFact,
    PluginDescriptor,
    PluginDiagnostic,
    PluginOutcome,
    ReviewContribution,
    ValidationDecision,
    ValidationResult,
)


_EXTENSIONS = (".py", ".pyi", ".pyw")
_MAX_FACTS_PER_FILE = 160
_MAX_EVIDENCE_REQUESTS = 40
_MODEL_RELATIONS = {
    "ForeignKey": "many-to-one",
    "ManyToManyField": "many-to-many",
    "OneToOneField": "one-to-one",
}
_VIEW_METHODS = frozenset({
    "delete", "get", "head", "options", "patch", "post", "put", "trace",
})
_FACT_KINDS = frozenset({
    "django-app-config",
    "django-installed-app",
    "django-middleware",
    "django-middleware-component",
    "django-model",
    "django-model-field",
    "django-model-relation",
    "django-signal-receiver",
    "django-url-configuration",
    "django-url-include",
    "django-url-route",
    "django-view",
    "django-view-action",
})
_RELATION_LABELS = {
    "django-app-config": ("app", "app config", "application"),
    "django-installed-app": ("app", "installed app"),
    "django-middleware": ("middleware",),
    "django-middleware-component": ("component", "middleware"),
    "django-model": ("model",),
    "django-model-field": ("field", "model field"),
    "django-model-relation": ("model relation", "relation", "relationship"),
    "django-signal-receiver": ("receiver", "signal receiver"),
    "django-url-configuration": ("url config", "url configuration"),
    "django-url-include": ("include", "url include"),
    "django-url-route": ("route", "url", "url route"),
    "django-view": ("view",),
    "django-view-action": ("action", "view action"),
}
_RELATION_STATES = {
    "django-app-config": ("is not configured",),
    "django-installed-app": ("is not installed", "is not registered"),
    "django-middleware": ("is not configured", "is not installed"),
    "django-middleware-component": ("is not configured",),
    "django-signal-receiver": ("is not connected", "is not registered"),
    "django-url-configuration": ("is not configured",),
    "django-url-include": ("is not included",),
    "django-url-route": ("is not configured", "is not registered"),
    "django-view-action": ("is not handled",),
}
_COMMON_RELATION_STATES = (
    "does not exist",
    "doesn't exist",
    "is absent",
    "is missing",
    "is not declared",
    "is not defined",
)
_ABSENCE_END = (
    r"(?=$|[.!?,;:]|\s+(?:and|because|despite|even|for|from|in|into|on|"
    r"so|therefore|when|while|with|without)\b)"
)


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
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, int, float, bool)):
        return str(node.value)
    if isinstance(node, (ast.Name, ast.Attribute)):
        return _name(node)
    return ""


def _string_literal(node: ast.AST | None) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ""


def _keyword(call: ast.Call, name: str) -> ast.AST | None:
    return next((keyword.value for keyword in call.keywords if keyword.arg == name), None)


def _assignment(node: ast.stmt) -> tuple[str, ast.AST | None]:
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        return _name(node.targets[0]), node.value
    if isinstance(node, ast.AnnAssign):
        return _name(node.target), node.value
    return "", None


def _sequence_values(node: ast.AST | None) -> tuple[ast.AST, ...]:
    """Return only statically visible values; dynamic expansions are ignored."""
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return tuple(node.elts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return (*_sequence_values(node.left), *_sequence_values(node.right))
    return ()


def _bound_names(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, (ast.List, ast.Tuple)):
        return tuple(name for item in node.elts for name in _bound_names(item))
    if isinstance(node, ast.Starred):
        return _bound_names(node.value)
    return ()


class _ScopeBindingCollector(ast.NodeVisitor):
    """Collect bindings in one scope without descending into nested scopes."""

    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        self.names.update(
            alias.asname or alias.name.split(".", 1)[0]
            for alias in node.names
        )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        self.names.update(
            alias.asname or alias.name
            for alias in node.names
            if alias.name != "*"
        )

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self.names.add(node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self.names.add(node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self.names.add(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        return

    def visit_ListComp(self, node: ast.ListComp) -> None:  # noqa: N802
        return

    def visit_SetComp(self, node: ast.SetComp) -> None:  # noqa: N802
        return

    def visit_DictComp(self, node: ast.DictComp) -> None:  # noqa: N802
        return

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:  # noqa: N802
        return

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:  # noqa: N802
        if node.name:
            self.names.add(node.name)
        for statement in node.body:
            self.visit(statement)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:  # noqa: N802
        if node.name:
            self.names.add(node.name)
        if node.pattern:
            self.visit(node.pattern)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:  # noqa: N802
        if node.name:
            self.names.add(node.name)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:  # noqa: N802
        if node.rest:
            self.names.add(node.rest)
        for pattern in node.patterns:
            self.visit(pattern)


def _scope_bound_names(statements: tuple[ast.stmt, ...] | list[ast.stmt]) -> frozenset[str]:
    collector = _ScopeBindingCollector()
    for statement in statements:
        collector.visit(statement)
    return frozenset(collector.names)


@dataclass(frozen=True)
class _ImportResolver:
    """Resolve only module-level names whose imports are statically visible."""

    bindings: dict[str, tuple[tuple[int, str | None], ...]]

    @classmethod
    def from_tree(cls, tree: ast.Module) -> _ImportResolver:
        mutable: dict[str, list[tuple[int, str | None]]] = {}

        def bind(name: str, line: int, canonical: str | None) -> None:
            mutable.setdefault(name, []).append((line, canonical))

        for statement in tree.body:
            if isinstance(statement, ast.Import):
                for alias in statement.names:
                    bound = alias.asname or alias.name.split(".", 1)[0]
                    canonical = alias.name if alias.asname else bound
                    bind(bound, statement.lineno, canonical)
                continue
            if isinstance(statement, ast.ImportFrom):
                for alias in statement.names:
                    if alias.name == "*":
                        continue
                    bound = alias.asname or alias.name
                    canonical = (
                        f"{statement.module}.{alias.name}"
                        if statement.level == 0 and statement.module
                        else None
                    )
                    bind(bound, statement.lineno, canonical)
                continue

            rebound = _scope_bound_names([statement])

            # The prior binding remains valid while decorators, bases, and the
            # assignment RHS are evaluated. Invalidate it for later statements.
            active_from = (getattr(statement, "end_lineno", None) or statement.lineno) + 1
            for name in sorted(rebound):
                bind(name, active_from, None)

        return cls({name: tuple(events) for name, events in mutable.items()})

    def resolve(self, node: ast.AST | None) -> str:
        if isinstance(node, ast.Name):
            line = getattr(node, "lineno", 0)
            canonical = ""
            for active_from, candidate in self.bindings.get(node.id, ()):
                if active_from > line:
                    break
                canonical = candidate or ""
            return canonical
        if isinstance(node, ast.Attribute):
            parent = self.resolve(node.value)
            return f"{parent}.{node.attr}" if parent else ""
        return ""


def _pattern_calls(
    node: ast.AST | None,
    imports: _ImportResolver,
) -> tuple[ast.Call, ...]:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return tuple(
            call
            for value in node.elts
            for call in _pattern_calls(value, imports)
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return (
            *_pattern_calls(node.left, imports),
            *_pattern_calls(node.right, imports),
        )
    if not isinstance(node, ast.Call):
        return ()
    operation = imports.resolve(node.func)
    if operation in {"django.urls.path", "django.urls.re_path"}:
        return (node,)
    if operation == "django.conf.urls.i18n.i18n_patterns":
        return tuple(
            call
            for value in node.args
            for call in _pattern_calls(value, imports)
        )
    return ()


def _attributes(**values: str) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(
        (key, value)
        for key, value in values.items()
        if value
    ))


def _module_name(path: str) -> str:
    module = path
    for extension in _EXTENSIONS:
        if module.casefold().endswith(extension):
            module = module[:-len(extension)]
            break
    module = module.replace("/", ".")
    return module.removesuffix(".__init__")


def _qualified(module: str, name: str) -> str:
    return f"{module}.{name}" if module else name


def _bounded_facts(facts: set[GraphFact]) -> tuple[GraphFact, ...]:
    """Keep direct calls bounded without starving a topology kind."""
    by_kind: dict[str, list[GraphFact]] = {}
    for fact in sorted(facts):
        by_kind.setdefault(fact.kind, []).append(fact)
    selected: list[GraphFact] = []
    offset = 0
    kinds = tuple(sorted(by_kind))
    while len(selected) < _MAX_FACTS_PER_FILE:
        added = False
        for kind in kinds:
            values = by_kind[kind]
            if offset < len(values):
                selected.append(values[offset])
                added = True
                if len(selected) == _MAX_FACTS_PER_FILE:
                    break
        if not added:
            break
        offset += 1
    return tuple(sorted(selected))


def _include_target(call: ast.Call, imports: _ImportResolver) -> str:
    if not call.args:
        return ""
    candidate = call.args[0]
    direct = _string_literal(candidate)
    if direct:
        return direct
    if isinstance(candidate, (ast.List, ast.Tuple)) and candidate.elts:
        return _string_literal(candidate.elts[0])
    if isinstance(candidate, (ast.Name, ast.Attribute)):
        return imports.resolve(candidate)
    return ""


def _fact_identifiers(fact: GraphFact) -> frozenset[str]:
    values = [fact.source, fact.target, *(value for _, value in fact.attributes)]
    identifiers: set[str] = set()
    for value in values:
        normalized = value.casefold().strip()
        if not normalized:
            continue
        identifiers.add(normalized)
        for separator in ("#", "/", ".", ":"):
            parts = normalized.replace("<", " ").replace(">", " ").split(separator)
            identifiers.update(part.strip(" _-()[],'\"") for part in parts)
    return frozenset(value for value in identifiers if len(value) >= 3)


def _mentions_identifier(message: str, identifier: str) -> bool:
    if identifier.replace("_", "").isalnum():
        return re.search(
            rf"(?<![a-z0-9_]){re.escape(identifier)}(?![a-z0-9_])",
            message,
        ) is not None
    return identifier in message


def _identifier_pattern(identifier: str) -> str:
    escaped = re.escape(identifier)
    if identifier.replace("_", "").isalnum():
        return rf"(?<![a-z0-9_]){escaped}(?![a-z0-9_])"
    return escaped


def _is_absence_claim(fact: GraphFact, message: str) -> bool:
    labels = _RELATION_LABELS.get(fact.kind, ())
    label_pattern = "|".join(re.escape(label) for label in labels)
    optional_label = rf"(?:\s+(?:{label_pattern}))?" if labels else ""
    states = (*_COMMON_RELATION_STATES, *_RELATION_STATES.get(fact.kind, ()))
    state_pattern = "|".join(re.escape(state) for state in states)
    named = r"(?:named\s+)?"
    for identifier in _fact_identifiers(fact):
        identifier_pattern = _identifier_pattern(identifier)
        if re.search(
            rf"{identifier_pattern}{optional_label}\s+(?:{state_pattern}){_ABSENCE_END}",
            message,
        ):
            return True
        if labels and re.search(
            rf"(?<![a-z0-9_])(?:missing|no)\s+(?:{label_pattern})\s+"
            rf"{named}{identifier_pattern}{_ABSENCE_END}",
            message,
        ):
            return True
    return False


def _is_model_base(canonical: str) -> bool:
    return canonical in {
        "django.db.models.Model",
        "django.db.models.base.Model",
    }


def _is_app_config(canonical: str) -> bool:
    return canonical in {
        "django.apps.AppConfig",
        "django.apps.config.AppConfig",
    }


def _model_field_type(canonical: str) -> str:
    if not canonical.startswith("django.db.models."):
        return ""
    field_type = canonical.rsplit(".", 1)[-1]
    if field_type.endswith("Field") or field_type in _MODEL_RELATIONS:
        return field_type
    return ""


def _view_base(canonical: str) -> str:
    if canonical.startswith("django.views.") or canonical.startswith("rest_framework."):
        base = canonical.rsplit(".", 1)[-1]
        if base.endswith(("View", "ViewSet")):
            return base
    return ""


def _is_function_view_decorator(canonical: str) -> bool:
    return (
        canonical.startswith("django.views.decorators.")
        or canonical in {
            "django.contrib.admin.views.decorators.staff_member_required",
            "django.contrib.auth.decorators.login_required",
            "django.contrib.auth.decorators.permission_required",
            "django.contrib.auth.decorators.user_passes_test",
            "rest_framework.decorators.api_view",
        }
    )


@dataclass(frozen=True)
class _CustomSignalResolver:
    bindings: dict[str, tuple[tuple[int, bool], ...]]

    @classmethod
    def from_tree(
        cls,
        tree: ast.Module,
        imports: _ImportResolver,
    ) -> _CustomSignalResolver:
        mutable: dict[str, list[tuple[int, bool]]] = {}
        for statement in tree.body:
            names: tuple[str, ...] = ()
            proven = False
            if isinstance(statement, ast.Assign):
                names = tuple(
                    name for target in statement.targets for name in _bound_names(target)
                )
                proven = (
                    len(names) == 1
                    and isinstance(statement.value, ast.Call)
                    and imports.resolve(statement.value.func) == "django.dispatch.Signal"
                )
            elif isinstance(statement, ast.AnnAssign):
                names = _bound_names(statement.target)
                proven = (
                    len(names) == 1
                    and isinstance(statement.value, ast.Call)
                    and imports.resolve(statement.value.func) == "django.dispatch.Signal"
                )
            elif isinstance(statement, ast.AugAssign):
                names = _bound_names(statement.target)
            elif isinstance(statement, ast.Import):
                names = tuple(
                    alias.asname or alias.name.split(".", 1)[0]
                    for alias in statement.names
                )
            elif isinstance(statement, ast.ImportFrom):
                names = tuple(
                    alias.asname or alias.name
                    for alias in statement.names
                    if alias.name != "*"
                )
            elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names = (statement.name,)
            elif isinstance(statement, ast.Delete):
                names = tuple(
                    name for target in statement.targets for name in _bound_names(target)
                )
            else:
                names = tuple(sorted(_scope_bound_names([statement])))
            active_from = (getattr(statement, "end_lineno", None) or statement.lineno) + 1
            for name in names:
                mutable.setdefault(name, []).append((active_from, proven))
        return cls({name: tuple(events) for name, events in mutable.items()})

    def contains(self, name: str, line: int) -> bool:
        proven = False
        for active_from, candidate in self.bindings.get(name, ()):
            if active_from > line:
                break
            proven = candidate
        return proven


def _is_proven_signal(
    node: ast.AST,
    imports: _ImportResolver,
    custom_signals: _CustomSignalResolver,
) -> bool:
    canonical = imports.resolve(node)
    if canonical.startswith("django.") and ".signals." in canonical:
        return True
    return (
        isinstance(node, ast.Name)
        and custom_signals.contains(node.id, getattr(node, "lineno", 0))
    )


def _root_name(node: ast.AST) -> str:
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else ""


def _function_bindings(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> frozenset[str]:
    arguments = {
        argument.arg
        for argument in (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        )
    }
    if node.args.vararg:
        arguments.add(node.args.vararg.arg)
    if node.args.kwarg:
        arguments.add(node.args.kwarg.arg)
    return frozenset((*arguments, *_scope_bound_names(node.body)))


class _ScopedCallCollector(ast.NodeVisitor):
    """Collect calls while recording lexical names that shadow module imports."""

    _MODULE_COMPOUNDS = (
        ast.AsyncFor,
        ast.AsyncWith,
        ast.For,
        ast.If,
        ast.Match,
        ast.Try,
        ast.TryStar,
        ast.While,
        ast.With,
    )

    def __init__(self) -> None:
        self.calls: list[tuple[ast.Call, frozenset[str]]] = []
        self._blocked: list[frozenset[str]] = [frozenset()]

    def visit_Module(self, node: ast.Module) -> None:  # noqa: N802
        for statement in node.body:
            # Conditional module-level bindings cannot be resolved exactly.
            # Skip calls inside those statements instead of guessing a branch.
            if isinstance(statement, self._MODULE_COMPOUNDS):
                continue
            self.visit(statement)

    def _visit_scope(self, body: list[ast.stmt], bindings: frozenset[str]) -> None:
        self._blocked.append(self._blocked[-1] | bindings)
        for statement in body:
            self.visit(statement)
        self._blocked.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._visit_scope(node.body, _function_bindings(node))

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._visit_scope(node.body, _function_bindings(node))

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self._visit_scope(node.body, _scope_bound_names(node.body))

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        self.calls.append((node, self._blocked[-1]))
        self.generic_visit(node)


def _scoped_calls(tree: ast.Module) -> tuple[tuple[ast.Call, frozenset[str]], ...]:
    collector = _ScopedCallCollector()
    collector.visit(tree)
    return tuple(collector.calls)


@dataclass(frozen=True)
class DjangoPlugin:
    descriptor: PluginDescriptor

    # Django relationships are emitted per file. There is deliberately no
    # repository session: URL imports and setting overrides cannot be restored
    # exactly from a partial snapshot without owning a complete Python resolver.
    def index_file(self, artifact: FileArtifact):
        if artifact.deleted or not artifact.path.casefold().endswith(_EXTENSIONS):
            return PluginOutcome.abstained()
        try:
            tree = ast.parse(artifact.content, filename=artifact.path, type_comments=True)
        except SyntaxError as exception:
            return PluginOutcome.failed(PluginDiagnostic(
                "django-python-parse-failed",
                f"SyntaxError at line {exception.lineno}: {exception.msg}",
                self.descriptor.id,
                path=artifact.path,
                recoverable=True,
            ))

        module = _module_name(artifact.path)
        filename = PurePosixPath(artifact.path).name.casefold()
        imports = _ImportResolver.from_tree(tree)
        custom_signals = _CustomSignalResolver.from_tree(tree, imports)
        facts: set[GraphFact] = set()
        self._settings_and_urls(tree, imports, artifact.path, module, facts)
        self._classes(tree, imports, artifact.path, module, filename, facts)
        self._function_views_and_signals(
            tree, imports, custom_signals, artifact.path, module, facts,
        )
        self._signal_connections(
            tree, imports, custom_signals, artifact.path, module, facts,
        )

        if not facts:
            return PluginOutcome.abstained()
        return PluginOutcome.handled(_bounded_facts(facts))

    @staticmethod
    def _settings_and_urls(
        tree: ast.Module,
        imports: _ImportResolver,
        path: str,
        module: str,
        facts: set[GraphFact],
    ) -> None:
        pattern_calls: list[ast.Call] = []
        for statement in tree.body:
            variable, value = _assignment(statement)
            if variable == "INSTALLED_APPS":
                for item in _sequence_values(value):
                    app = _string_literal(item)
                    if app:
                        facts.add(GraphFact(
                            "django-installed-app", module, "installs", app,
                            path, getattr(item, "lineno", statement.lineno),
                        ))
            elif variable == "MIDDLEWARE":
                for item in _sequence_values(value):
                    middleware = _string_literal(item)
                    if middleware:
                        facts.add(GraphFact(
                            "django-middleware", module, "uses", middleware,
                            path, getattr(item, "lineno", statement.lineno),
                        ))
            elif variable == "ROOT_URLCONF":
                url_configuration = _string_literal(value)
                if url_configuration:
                    facts.add(GraphFact(
                        "django-url-configuration", module, "uses",
                        url_configuration, path, statement.lineno,
                    ))
            elif variable == "urlpatterns":
                pattern_calls.extend(_pattern_calls(value, imports))
            elif isinstance(statement, ast.AugAssign) and _name(statement.target) == "urlpatterns":
                pattern_calls.extend(_pattern_calls(statement.value, imports))

        for call in pattern_calls:
            if len(call.args) < 2:
                continue
            operation = imports.resolve(call.func).rsplit(".", 1)[-1]
            if not (
                isinstance(call.args[0], ast.Constant)
                and isinstance(call.args[0].value, str)
            ):
                continue
            route = _string_literal(call.args[0])
            route = route or "/"
            destination = call.args[1]
            route_name = _string_literal(_keyword(call, "name"))
            source = f"{module}:{route}"
            if (
                isinstance(destination, ast.Call)
                and imports.resolve(destination.func) == "django.urls.include"
            ):
                included = _include_target(destination, imports)
                if not included:
                    continue
                namespace = _string_literal(_keyword(destination, "namespace"))
                facts.add(GraphFact(
                    "django-url-include", source, "includes", included,
                    path, call.lineno,
                    _attributes(namespace=namespace, pattern=operation, route_name=route_name),
                ))
                continue
            if isinstance(destination, ast.Call) and not (
                isinstance(destination.func, ast.Attribute)
                and destination.func.attr == "as_view"
            ):
                continue
            view = _name(destination)
            if not view:
                continue
            facts.add(GraphFact(
                "django-url-route", source, "dispatches-to", view,
                path, call.lineno,
                _attributes(pattern=operation, route_name=route_name),
            ))

    @staticmethod
    def _classes(
        tree: ast.Module,
        imports: _ImportResolver,
        path: str,
        module: str,
        filename: str,
        facts: set[GraphFact],
    ) -> None:
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            qualified = _qualified(module, node.name)
            base_names = {imports.resolve(base) for base in node.bases}

            if any(_is_app_config(base) for base in base_names):
                values = {
                    name: _string_literal(value)
                    for statement in node.body
                    for name, value in (_assignment(statement),)
                    if name in {"label", "name", "verbose_name"}
                }
                facts.add(GraphFact(
                    "django-app-config", qualified, "configures",
                    values.get("name") or qualified, path, node.lineno,
                    _attributes(label=values.get("label", ""), verbose_name=values.get("verbose_name", "")),
                ))

            if any(_is_model_base(base) for base in base_names):
                facts.add(GraphFact(
                    "django-model", module, "declares", qualified, path, node.lineno,
                ))
                for statement in node.body:
                    field_name, value = _assignment(statement)
                    if not field_name or not isinstance(value, ast.Call):
                        continue
                    field_type = _model_field_type(imports.resolve(value.func))
                    if not field_type:
                        continue
                    field = f"{qualified}.{field_name}"
                    facts.add(GraphFact(
                        "django-model-field", qualified, "declares", field,
                        path, statement.lineno, _attributes(field_type=field_type),
                    ))
                    relation = _MODEL_RELATIONS.get(field_type)
                    if relation is None or not value.args:
                        continue
                    target = _literal(value.args[0])
                    if not target:
                        continue
                    facts.add(GraphFact(
                        "django-model-relation", field, relation, target,
                        path, statement.lineno,
                        _attributes(
                            on_delete=_literal(_keyword(value, "on_delete")),
                            related_name=_literal(_keyword(value, "related_name")),
                        ),
                    ))

            view_base = next(filter(None, map(_view_base, sorted(base_names))), "")
            if view_base:
                facts.add(GraphFact(
                    "django-view", module, "declares", qualified, path, node.lineno,
                    _attributes(base=view_base),
                ))
                for statement in node.body:
                    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)) and statement.name in _VIEW_METHODS:
                        facts.add(GraphFact(
                            "django-view-action", qualified, "handles",
                            statement.name.upper(), path, statement.lineno,
                        ))

            if filename == "middleware.py":
                hooks = tuple(sorted(
                    statement.name
                    for statement in node.body
                    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and (statement.name == "__call__" or statement.name.startswith("process_"))
                ))
                if hooks:
                    facts.add(GraphFact(
                        "django-middleware-component", module, "declares", qualified,
                        path, node.lineno, (("hooks", ",".join(hooks)),),
                    ))

    @staticmethod
    def _function_views_and_signals(
        tree: ast.Module,
        imports: _ImportResolver,
        custom_signals: _CustomSignalResolver,
        path: str,
        module: str,
        facts: set[GraphFact],
    ) -> None:
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            qualified = _qualified(module, node.name)
            decorators = tuple(
                imports.resolve(decorator.func if isinstance(decorator, ast.Call) else decorator)
                for decorator in node.decorator_list
            )
            if any(_is_function_view_decorator(decorator) for decorator in decorators):
                facts.add(GraphFact(
                    "django-view", module, "declares", qualified, path, node.lineno,
                    (("style", "function"),),
                ))
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                if (
                    imports.resolve(decorator.func) != "django.dispatch.receiver"
                    or not decorator.args
                    or not _is_proven_signal(decorator.args[0], imports, custom_signals)
                ):
                    continue
                signal = _name(decorator.args[0])
                if not signal:
                    continue
                facts.add(GraphFact(
                    "django-signal-receiver", signal, "notifies", qualified,
                    path, decorator.lineno,
                    _attributes(sender=_literal(_keyword(decorator, "sender"))),
                ))

    @staticmethod
    def _signal_connections(
        tree: ast.Module,
        imports: _ImportResolver,
        custom_signals: _CustomSignalResolver,
        path: str,
        module: str,
        facts: set[GraphFact],
    ) -> None:
        for node, blocked_names in _scoped_calls(tree):
            if not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "connect" or not node.args:
                continue
            if _root_name(node.func.value) in blocked_names:
                continue
            if not _is_proven_signal(node.func.value, imports, custom_signals):
                continue
            signal = _name(node.func.value)
            receiver = _name(node.args[0])
            if not signal or not receiver:
                continue
            if "." not in receiver:
                receiver = _qualified(module, receiver)
            facts.add(GraphFact(
                "django-signal-receiver", signal, "notifies", receiver,
                path, node.lineno,
                _attributes(sender=_literal(_keyword(node, "sender"))),
            ))

    def review(self, paths: tuple[str, ...]):
        selected = tuple(sorted(
            path for path in paths
            if path.casefold().endswith(_EXTENSIONS)
        ))[:_MAX_EVIDENCE_REQUESTS]
        if not selected:
            return PluginOutcome.abstained()
        rules = tuple(sorted((
            "Resolve Django URL dispatch through urlpatterns, include prefixes, and exact view declarations before judging endpoint reachability.",
            "Treat settings, app configuration, middleware, model relations, and signal receivers as topology context; their presence alone is not defect proof.",
        )))
        return PluginOutcome.handled(ReviewContribution(
            rules=rules,
            evidence_requests=tuple(EvidenceRequest(
                "django-topology",
                path,
                "exact Django settings, app, middleware, URL, view, model-relation, and signal facts",
            ) for path in selected),
        ))

    def validate(self, claim: CandidateClaim):
        requested_kind = claim.claim_kind or claim.category
        if not requested_kind.startswith("django-"):
            return PluginOutcome.abstained()
        if not claim.path.casefold().endswith(_EXTENSIONS):
            return PluginOutcome.abstained()

        if requested_kind == "django-topology":
            expected_kinds = _FACT_KINDS
        elif requested_kind in _FACT_KINDS:
            expected_kinds = frozenset({requested_kind})
        else:
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.INSUFFICIENT_EVIDENCE,
                "django-unknown-fact-kind",
                "The Django claim kind is not owned by an exact validator.",
            ))
        matching = tuple(
            fact for fact in claim.evidence
            if fact.kind in expected_kinds
            and claim.path in {fact.path, *fact.related_paths}
        )
        message = claim.message.casefold()
        relevant = tuple(
            fact for fact in matching
            if any(
                _mentions_identifier(message, identifier)
                for identifier in _fact_identifiers(fact)
            )
        )
        if any(_is_absence_claim(fact, message) for fact in relevant):
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.REJECT,
                "django-absence-contradicted",
                "The candidate claims Django topology is absent, but an exact matching framework fact exists.",
            ))
        if relevant:
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.INSUFFICIENT_EVIDENCE,
                "django-topology-not-defect-proof",
                "The cited Django relationship exists, but structural presence alone does not prove defective behavior.",
            ))
        if matching:
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.INSUFFICIENT_EVIDENCE,
                "django-cited-identifier-mismatch",
                "Django topology facts exist for the path, but their identifiers do not match the candidate message.",
            ))
        return PluginOutcome.handled(ValidationResult(
            ValidationDecision.INSUFFICIENT_EVIDENCE,
            "django-evidence-unavailable",
            "No exact matching Django topology evidence was supplied for this framework claim.",
        ))


def create_plugin(descriptor: PluginDescriptor) -> DjangoPlugin:
    return DjangoPlugin(descriptor)
