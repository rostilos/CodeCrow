from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from pathlib import PurePosixPath

from codecrow_plugins import PluginDiagnostic, SymbolDefinition

from .architecture import ModuleRecord, safe_xml
from .javascript import OptionalJavaScriptEnrichmentError
from .layout import LayoutArgument
from .resolution_models import (
    TemplatePhpCall,
    ThemeRecord,
    _PHP_TEMPLATE_CALL_REFERENCE,
    _path_under,
)


class RepositorySourceIndex:
    """Source lookup, symbol indexes, and recoverable input diagnostics."""

    def __init__(
        self,
        plugin_id: str,
        artifacts: dict[str, str],
        symbols: tuple[SymbolDefinition, ...],
    ) -> None:
        self.plugin_id = plugin_id
        self.artifacts = artifacts
        self.symbols = symbols
        self._module_sources: tuple[ModuleRecord, ...] | None = None
        self._modules_by_root: dict[str, ModuleRecord] = {}
        self._modules_by_name: dict[str, ModuleRecord] = {}
        self._theme_sources: tuple[ThemeRecord, ...] | None = None
        self._themes_by_root: dict[str, ThemeRecord] = {}
        by_name: dict[str, list[SymbolDefinition]] = defaultdict(list)
        by_casefold: dict[str, list[SymbolDefinition]] = defaultdict(list)
        by_path: dict[str, list[SymbolDefinition]] = defaultdict(list)
        for symbol in symbols:
            by_name[symbol.qualified_name].append(symbol)
            by_casefold[symbol.qualified_name.lstrip("\\").casefold()].append(symbol)
            by_path[symbol.path].append(symbol)
        self.symbols_by_name = {key: tuple(values) for key, values in by_name.items()}
        self.symbols_by_casefold = {key: tuple(values) for key, values in by_casefold.items()}
        self.symbols_by_path = {key: tuple(values) for key, values in by_path.items()}
        self.configured_modules: dict[str, bool] = {}
        self._roots: dict[str, object] = {}
        self.diagnostics: list[PluginDiagnostic] = []
        self.invalid_paths: set[str] = set()
        self._optional_enrichment_diagnostics: set[tuple[str, str | None]] = set()
        self._template_metadata_diagnostics: set[tuple[str, str]] = set()
        self._template_php_call_cache: dict[tuple[str, str], tuple[TemplatePhpCall, ...]] = {}
        by_component: dict[str, list[str]] = defaultdict(list)
        for path in artifacts:
            if "/ui_component/" in f"/{path}" and path.endswith(".xml"):
                by_component[PurePosixPath(path).stem].append(path)
        self.ui_component_paths_by_name = {
            key: tuple(values) for key, values in by_component.items()
        }

    def record_optional_enrichment_failure(
        self,
        stage_name: str,
        exception: OptionalJavaScriptEnrichmentError,
        path: str | None = None,
    ) -> None:
        """Record an expected optional frontend degradation once per source."""

        diagnostic_path = path if exception.source_specific else None
        identity = (exception.diagnostic_code, diagnostic_path)
        if identity in self._optional_enrichment_diagnostics:
            return
        self._optional_enrichment_diagnostics.add(identity)
        location = f" for {path}" if path else ""
        self.diagnostics.append(PluginDiagnostic(
            exception.diagnostic_code,
            (
                f"Magento {stage_name} enrichment skipped{location}: "
                f"{exception}"
            ),
            self.plugin_id,
            diagnostic_path,
            recoverable=True,
        ))

    def optional_frontend_source(
        self,
        stage_name: str,
        path: str,
        extractor,
    ):
        """Skip only malformed sources; propagate stage-wide unavailability."""

        try:
            return extractor(self.artifacts[path])
        except OptionalJavaScriptEnrichmentError as exception:
            if not exception.source_specific:
                raise
            self.record_optional_enrichment_failure(
                stage_name,
                exception,
                path,
            )
            return ()

    def xml(self, path: str):
        if path in self._roots:
            return self._roots[path]
        root, diagnostic = safe_xml(self.plugin_id, path, self.artifacts[path])
        if diagnostic:
            self.diagnostics.append(diagnostic)
            self.invalid_paths.add(path)
        self._roots[path] = root
        return root

    def _index_modules(self, modules: tuple[ModuleRecord, ...]) -> None:
        if modules is self._module_sources:
            return
        self._module_sources = modules
        self._modules_by_root = {}
        self._modules_by_name = {}
        for module in modules:
            # Equal-length roots keep the same first declaration as max().
            self._modules_by_root.setdefault(module.root, module)
            self._modules_by_name.setdefault(module.name, module)

    @staticmethod
    def _owning_root(path: str, roots: dict) -> str:
        while path:
            if path in roots:
                return path
            path = path.rpartition("/")[0]
        return ""

    def module_for_path(self, path: str, modules: tuple[ModuleRecord, ...]) -> ModuleRecord | None:
        self._index_modules(modules)
        return self._modules_by_root.get(self._owning_root(path, self._modules_by_root))

    def ordered_configs(
        self,
        filename: str,
        modules: tuple[ModuleRecord, ...],
        area: str,
    ) -> tuple[tuple[str, ModuleRecord | None, int], ...]:
        result: list[tuple[str, ModuleRecord | None, int]] = []
        initial = f"app/etc/{filename}"
        if initial in self.artifacts:
            result.append((initial, None, -1))
        for module in modules:
            if not module.enabled:
                continue
            global_path = _path_under(module.root, f"etc/{filename}")
            if global_path in self.artifacts:
                result.append((global_path, module, module.order))
        if area not in {"global", "initial"}:
            for module in modules:
                if not module.enabled:
                    continue
                scoped_path = _path_under(module.root, f"etc/{area}/{filename}")
                if scoped_path in self.artifacts:
                    result.append((scoped_path, module, module.order))
        return tuple(result)

    def symbol(self, qualified_name: str) -> SymbolDefinition | None:
        values = self.symbols_by_name.get(qualified_name.lstrip("\\"), ())
        return values[0] if values else None

    def unique_symbol_casefold(
        self,
        qualified_name: str,
    ) -> SymbolDefinition | None:
        values = self.symbols_by_casefold.get(
            qualified_name.lstrip("\\").casefold(),
            (),
        )
        return values[0] if len(values) == 1 else None

    @staticmethod
    def method_attributes(
        symbol: SymbolDefinition,
        method: str,
    ) -> tuple[str, dict[str, str]] | None:
        declared = next(
            (
                name for name in symbol.methods
                if name.casefold() == method.casefold()
            ),
            None,
        )
        if declared is None:
            return None
        prefix = f"method:{declared}:"
        return declared, {
            key.removeprefix(prefix): value
            for key, value in symbol.attributes
            if key.startswith(prefix)
        }

    def symbol_path(self, qualified_name: str) -> str:
        if not qualified_name:
            return ""
        symbol = self.symbol(qualified_name.split("::", 1)[0])
        return symbol.path if symbol else ""

    def method_symbol(
        self,
        symbol: SymbolDefinition,
        method: str,
    ) -> tuple[SymbolDefinition, str] | None:
        queue = deque([symbol])
        seen: set[str] = set()
        while queue:
            candidate = queue.popleft()
            if candidate.qualified_name in seen:
                continue
            seen.add(candidate.qualified_name)
            declaration = self.method_attributes(candidate, method)
            if (
                declaration is not None
                and declaration[1].get("visibility", "public") == "public"
            ):
                declared, _ = declaration
                return candidate, declared
            queue.extend(
                parent
                for parent_name in candidate.parents
                if (parent := self.unique_symbol_casefold(parent_name)) is not None
            )
        return None

    def is_view_model_argument(
        self,
        argument_name: str,
        symbol: SymbolDefinition | None,
    ) -> bool:
        leaf_name = argument_name.rsplit(".", 1)[-1]
        if re.sub(r"[^a-z0-9]", "", leaf_name.casefold()) == "viewmodel":
            return True
        expected = (
            r"magento\framework\view\element\block\argumentinterface"
        )
        queue = deque([symbol] if symbol is not None else [])
        seen: set[str] = set()
        while queue:
            candidate = queue.popleft()
            normalized = candidate.qualified_name.lstrip("\\").casefold()
            if normalized in seen:
                continue
            seen.add(normalized)
            if normalized == expected:
                return True
            for parent_name in candidate.parents:
                if parent_name.lstrip("\\").casefold() == expected:
                    return True
                parent = self.unique_symbol_casefold(parent_name)
                if parent is not None:
                    queue.append(parent)
        return False

    @staticmethod
    def layout_argument_for_call(
        call: TemplatePhpCall,
        arguments: tuple[LayoutArgument, ...],
    ) -> str:
        requested = ""
        if call.method.casefold() in {"getdata", "hasdata"}:
            requested = dict(call.literal_arguments).get(0, "")
        else:
            match = re.fullmatch(r"(?:get|has)([A-Z][A-Za-z0-9]*)", call.method)
            if match is not None:
                requested = re.sub(
                    r"(?<!^)(?=[A-Z])",
                    "_",
                    match.group(1),
                ).casefold()
        if not requested:
            return ""
        matches = tuple(
            argument.name
            for argument in arguments
            if argument.name == requested
        )
        return matches[0] if len(matches) == 1 else ""

    def template_php_calls(
        self,
        path: str,
        receiver: str,
    ) -> tuple[TemplatePhpCall, ...]:
        """Read syntax-proven PHTML calls published by the neutral PHP plugin."""

        cache_key = (path, receiver)
        cached = self._template_php_call_cache.get(cache_key)
        if cached is not None:
            return cached
        records: set[TemplatePhpCall] = set()
        for symbol in self.symbols_by_path.get(path, ()):
            if symbol.kind != "template":
                continue
            for key, value in symbol.attributes:
                if not key.startswith(_PHP_TEMPLATE_CALL_REFERENCE):
                    continue
                try:
                    payload = json.loads(value)
                except (TypeError, json.JSONDecodeError):
                    self.record_template_metadata_diagnostic(
                        path,
                        key,
                        "invalid JSON",
                    )
                    continue
                if not isinstance(payload, dict):
                    self.record_template_metadata_diagnostic(
                        path,
                        key,
                        "value must be an object",
                    )
                    continue
                call_receiver = payload.get("receiver")
                method = payload.get("method")
                call_line = payload.get("line")
                literal_arguments = payload.get(
                    "literalStringArguments",
                    {},
                )
                if (
                    not isinstance(call_receiver, str)
                    or not isinstance(method, str)
                    or not isinstance(call_line, int)
                    or call_line < 1
                    or not isinstance(literal_arguments, dict)
                    or any(
                        not isinstance(position, str)
                        or not position.isdigit()
                        or not isinstance(argument, str)
                        for position, argument in literal_arguments.items()
                    )
                ):
                    self.record_template_metadata_diagnostic(
                        path,
                        key,
                        "object has invalid fields",
                    )
                    continue
                if call_receiver != receiver:
                    continue
                records.add(TemplatePhpCall(
                    receiver=call_receiver,
                    method=method,
                    line=call_line,
                    literal_arguments=tuple(sorted(
                        (int(position), argument)
                        for position, argument
                        in literal_arguments.items()
                    )),
                ))
        result = tuple(sorted(records))
        self._template_php_call_cache[cache_key] = result
        return result

    def record_template_metadata_diagnostic(
        self,
        path: str,
        key: str,
        reason: str,
    ) -> None:
        identity = (path, key)
        if identity in self._template_metadata_diagnostics:
            return
        self._template_metadata_diagnostics.add(identity)
        self.diagnostics.append(PluginDiagnostic(
            code="magento-invalid-php-template-call-metadata",
            message=(
                f"{path}: ignored PHP template call metadata {key!r}: "
                f"{reason}"
            ),
            plugin_id=self.plugin_id,
            path=path,
            recoverable=True,
        ))

    def theme_for_path(
        self,
        path: str,
        themes: tuple[ThemeRecord, ...],
    ) -> ThemeRecord | None:
        if themes is not self._theme_sources:
            self._theme_sources = themes
            self._themes_by_root = {}
            for theme in themes:
                self._themes_by_root.setdefault(theme.root, theme)
        return self._themes_by_root.get(self._owning_root(path, self._themes_by_root))

    def is_deployed_view_source(
        self,
        path: str,
        modules: tuple[ModuleRecord, ...],
        themes: tuple[ThemeRecord, ...],
    ) -> bool:
        """Match Magento's enabled-module filter while retaining registered themes."""
        theme = self.theme_for_path(path, themes)
        if theme is not None:
            theme_module = self.theme_module(path, theme)
            if theme_module:
                self._index_modules(modules)
                module = self._modules_by_name.get(theme_module)
                if module is not None:
                    return module.enabled
                if theme_module in self.configured_modules:
                    return self.configured_modules[theme_module]
                # A standalone theme package has no deployment config from
                # which to prove installed modules. Retain its explicitly
                # named override directories without inventing ModuleRecords;
                # exact module-dependent resolution continues to abstain.
                return not modules and not self.configured_modules
            return True
        module = self.module_for_path(path, modules)
        return module is not None and module.enabled

    @staticmethod
    def theme_chain(
        theme: ThemeRecord,
        themes: tuple[ThemeRecord, ...],
    ) -> tuple[ThemeRecord, ...]:
        """Return the exact child-to-parent fallback chain for one theme."""
        by_identity = {
            (candidate.area, candidate.name): candidate
            for candidate in themes
        }
        chain: list[ThemeRecord] = []
        seen: set[tuple[str, str]] = set()
        current: ThemeRecord | None = theme
        while current is not None:
            identity = (current.area, current.name)
            if identity in seen:
                break
            seen.add(identity)
            chain.append(current)
            current = (
                by_identity.get((current.area, current.parent))
                if current.parent
                else None
            )
        return tuple(chain)

    @staticmethod
    def theme_module(path: str, theme: ThemeRecord | None) -> str:
        if theme is None:
            return ""
        relative = path[len(theme.root):].lstrip("/")
        first = relative.split("/", 1)[0]
        return first if "_" in first else ""

    def template_paths(
        self,
        template: str,
        area: str,
        modules: tuple[ModuleRecord, ...],
        themes: tuple[ThemeRecord, ...],
        source_theme: ThemeRecord | None = None,
    ) -> tuple[str, ...]:
        if not template or "::" not in template:
            return ()
        module_name, relative = template.split("::", 1)
        module = next((item for item in modules if item.name == module_name), None)
        theme_only_selection = (
            source_theme is not None
            and not modules
            and not self.configured_modules
        )
        if module is not None and not module.enabled:
            return ()
        if (
            module is None
            and not self.configured_modules.get(module_name, False)
            and not theme_only_selection
        ):
            return ()
        paths: set[str] = set()
        if module is not None:
            for candidate_area in (area, "base"):
                candidate = _path_under(
                    module.root,
                    f"view/{candidate_area}/templates/{relative}",
                )
                if candidate in self.artifacts:
                    paths.add(candidate)
        theme_candidates = (
            self.theme_chain(source_theme, themes)
            if source_theme is not None
            else themes
        )
        for theme in theme_candidates:
            if theme.area != area:
                continue
            candidate = _path_under(theme.root, f"{module_name}/templates/{relative}")
            if candidate in self.artifacts:
                paths.add(candidate)
        return tuple(sorted(paths))

    def selected_template_path(
        self,
        template: str,
        area: str,
        modules: tuple[ModuleRecord, ...],
        themes: tuple[ThemeRecord, ...],
        source_theme: ThemeRecord | None = None,
    ) -> str:
        """Return one runtime-selected PHTML path or abstain if theme is unknown."""

        if not template or "::" not in template:
            return ""
        module_name, relative = template.split("::", 1)
        module = next(
            (item for item in modules if item.name == module_name),
            None,
        )
        theme_only_selection = (
            source_theme is not None
            and not modules
            and not self.configured_modules
        )
        if module is not None and not module.enabled:
            return ""
        if (
            module is None
            and not self.configured_modules.get(module_name, False)
            and not theme_only_selection
        ):
            return ""

        if source_theme is not None:
            for theme in self.theme_chain(source_theme, themes):
                if theme.area != area:
                    continue
                candidate = _path_under(
                    theme.root,
                    f"{module_name}/templates/{relative}",
                )
                if candidate in self.artifacts:
                    return candidate
        else:
            # A module-owned layout can run under any configured store theme.
            # If any installed theme overrides this exact template identity,
            # repository state cannot select the runtime source.
            if any(
                theme.area == area
                and _path_under(
                    theme.root,
                    f"{module_name}/templates/{relative}",
                ) in self.artifacts
                for theme in themes
            ):
                return ""

        if module is not None:
            for candidate_area in (area, "base"):
                candidate = _path_under(
                    module.root,
                    f"view/{candidate_area}/templates/{relative}",
                )
                if candidate in self.artifacts:
                    return candidate
        return ""

    def ui_asset_paths(
        self,
        identifier: str,
        area: str,
        is_template: bool,
        modules: tuple[ModuleRecord, ...],
        themes: tuple[ThemeRecord, ...],
        source_theme: ThemeRecord | None = None,
    ) -> tuple[str, ...]:
        if "/" not in identifier:
            return ()
        module_name, separator, relative = identifier.partition("/")
        if not separator or "_" not in module_name:
            return ()
        extension = ".html" if is_template else ".js"
        relative_path = relative if relative.endswith(extension) else relative + extension
        paths: set[str] = set()
        module = next((item for item in modules if item.name == module_name), None)
        theme_only_selection = (
            source_theme is not None
            and not modules
            and not self.configured_modules
        )
        if module is not None and not module.enabled:
            return ()
        if (
            module is None
            and not self.configured_modules.get(module_name, False)
            and not theme_only_selection
        ):
            return ()
        if module is not None:
            for candidate_area in (area, "base"):
                candidate = _path_under(
                    module.root,
                    f"view/{candidate_area}/web/{relative_path}",
                )
                if candidate in self.artifacts:
                    paths.add(candidate)
        theme_candidates = (
            self.theme_chain(source_theme, themes)
            if source_theme is not None
            else themes
        )
        for theme in theme_candidates:
            if theme.area != area:
                continue
            candidate = _path_under(
                theme.root,
                f"{module_name}/web/{relative_path}",
            )
            if candidate in self.artifacts:
                paths.add(candidate)
        return tuple(sorted(paths))
