from __future__ import annotations

import re

from codecrow_plugins import ArchitecturePacket, GraphFact, SymbolDefinition

from .syntax import (
    _BUILTIN_TYPES,
    _CHAINED_INSTANCE_CALL_REFERENCE,
    _CONSTRUCTION_REFERENCE,
    _INSTANCE_CALL_REFERENCE,
    _STATIC_CALL_REFERENCE,
    _decode_chained_reference,
    _decode_reference,
)


class PhpRelationResolver:
    """Join exact PHP references to in-repository declaration contracts."""

    def __init__(self, plugin_id: str, symbols: tuple[SymbolDefinition, ...]) -> None:
        self.plugin_id = plugin_id
        self.symbols = symbols

    def relation_packets(self) -> tuple[ArchitecturePacket, ...]:
        """Resolve exact in-repository PHP code relations without path guessing."""
        symbols_by_name: dict[str, list[SymbolDefinition]] = {}
        for symbol in self.symbols:
            symbols_by_name.setdefault(
                symbol.qualified_name.casefold(),
                [],
            ).append(symbol)

        facts_by_path: dict[str, set[GraphFact]] = {}
        related_by_path: dict[str, set[str]] = {}
        for source in self.symbols:
            attributes = dict(source.attributes)
            relations: set[
                tuple[
                    str,
                    str,
                    str,
                    int,
                    tuple[tuple[str, str], ...],
                ]
            ] = set()
            chained_relations: set[
                tuple[
                    int,
                    str,
                    tuple[str, ...],
                    str,
                    str,
                    str,
                ]
            ] = set()

            parent_class = attributes.get("php-parent-class")
            if parent_class:
                relations.add((
                    "php-inheritance",
                    "extends",
                    parent_class,
                    source.line,
                    (),
                ))
            interface_relation = (
                "extends"
                if source.kind == "interface"
                else "implements"
            )
            relations.update(
                (
                    "php-inheritance",
                    interface_relation,
                    target,
                    source.line,
                    (),
                )
                for key, target in source.attributes
                if key.startswith(("php-parent-interface:", "php-interface:"))
            )
            relations.update(
                (
                    "php-trait-use",
                    "uses-trait",
                    target,
                    source.line,
                    (),
                )
                for key, target in source.attributes
                if key.startswith("php-trait:")
            )
            relations.update(
                (
                    "php-constructor-dependency",
                    "constructor-requires",
                    target,
                    source.line,
                    (),
                )
                for target in source.constructor_types
            )
            for key, value in source.attributes:
                if key.startswith(_CONSTRUCTION_REFERENCE):
                    (
                        line_number,
                        target,
                        _,
                        caller,
                        _,
                    ) = _decode_reference(value)
                    call_attributes = (
                        (("callerMethod", caller),)
                        if caller
                        else ()
                    )
                    relations.add((
                        "php-construction-relation",
                        "constructs",
                        target,
                        line_number,
                        call_attributes,
                    ))
                elif key.startswith(_STATIC_CALL_REFERENCE):
                    (
                        line_number,
                        target,
                        method,
                        caller,
                        _,
                    ) = _decode_reference(value)
                    call_attributes = tuple(sorted((
                        *((("callerMethod", caller),) if caller else ()),
                        ("retrievalIdentifier:targetMethod", method),
                        ("targetMethod", method),
                    )))
                    relations.add((
                        "php-static-call-relation",
                        "calls-static",
                        target,
                        line_number,
                        call_attributes,
                    ))
                elif key.startswith(_INSTANCE_CALL_REFERENCE):
                    (
                        line_number,
                        target,
                        method,
                        caller,
                        receiver_resolution,
                    ) = _decode_reference(value)
                    call_attributes = tuple(sorted((
                        *((("callerMethod", caller),) if caller else ()),
                        *(
                            (("receiverResolution", receiver_resolution),)
                            if receiver_resolution
                            else ()
                        ),
                        ("retrievalIdentifier:targetMethod", method),
                        ("targetMethod", method),
                    )))
                    relations.add((
                        "php-instance-call-relation",
                        "calls-instance",
                        target,
                        line_number,
                        call_attributes,
                    ))
                elif key.startswith(_CHAINED_INSTANCE_CALL_REFERENCE):
                    chained_relations.add(_decode_chained_reference(value))

            resolved_relations: dict[
                tuple[str, str, str, tuple[tuple[str, str], ...]],
                tuple[SymbolDefinition, int],
            ] = {}
            for (
                kind,
                relation,
                target_name,
                line_number,
                relation_attributes,
            ) in sorted(relations):
                candidates = symbols_by_name.get(target_name.casefold(), ())
                if len(candidates) != 1:
                    continue
                target = candidates[0]
                if kind == "php-trait-use":
                    source_methods = {
                        method.casefold() for method in source.methods
                    }
                    target_methods = {
                        method.casefold() for method in target.methods
                    }
                    constructor_attributes: list[tuple[str, str]] = []
                    if "__construct" in source_methods:
                        constructor_attributes.append((
                            "retrievalIdentifier:consumerConstructor",
                            "__construct",
                        ))
                    if "__construct" in target_methods:
                        constructor_attributes.append((
                            "retrievalIdentifier:traitConstructor",
                            "__construct",
                        ))
                    if (
                        "__construct" in source_methods
                        and "__construct" in target_methods
                    ):
                        constructor_attributes.extend((
                            ("constructorResolution", "class-method-precedence"),
                            ("resolvedMethod", "__construct"),
                        ))
                    relation_attributes = tuple(sorted((
                        *relation_attributes,
                        *constructor_attributes,
                    )))
                if target.path == source.path:
                    if kind not in {
                        "php-instance-call-relation",
                        "php-static-call-relation",
                    }:
                        continue
                    target_method = dict(relation_attributes).get(
                        "targetMethod",
                        "",
                    )
                    (
                        method_contract,
                        _,
                    ) = self.declared_method_contract(
                        target,
                        target_method,
                        symbols_by_name,
                    )
                    if (
                        not target_method
                        or dict(method_contract).get(
                            "targetMethodDeclared"
                        ) != "true"
                    ):
                        continue
                    facts_by_path.setdefault(source.path, set()).add(GraphFact(
                        kind="php-intra-class-call-relation",
                        source=source.qualified_name,
                        relation=relation,
                        target=target.qualified_name,
                        path=source.path,
                        line=line_number,
                        attributes=tuple(sorted((
                            ("sourceKind", source.kind),
                            ("targetKind", target.kind),
                            *relation_attributes,
                            *method_contract,
                        ))),
                    ))
                    continue
                identity = (
                    kind,
                    relation,
                    target.qualified_name,
                    relation_attributes,
                )
                existing = resolved_relations.get(identity)
                if existing is None or line_number < existing[1]:
                    resolved_relations[identity] = (target, line_number)

            for (
                kind,
                relation,
                _,
                relation_attributes,
            ), (target, line_number) in sorted(resolved_relations.items()):
                target_method = dict(relation_attributes).get(
                    "targetMethod",
                    "",
                )
                (
                    method_contract,
                    method_declaration_path,
                ) = self.declared_method_contract(
                    target,
                    target_method,
                    symbols_by_name,
                )
                fact_related_paths = tuple(sorted({
                    target.path,
                    *(
                        (method_declaration_path,)
                        if method_declaration_path
                        else ()
                    ),
                }))
                facts_by_path.setdefault(source.path, set()).add(GraphFact(
                    kind=kind,
                    source=source.qualified_name,
                    relation=relation,
                    target=target.qualified_name,
                    path=source.path,
                    line=line_number,
                    attributes=tuple(sorted((
                        ("sourceKind", source.kind),
                        ("targetKind", target.kind),
                        *relation_attributes,
                        *method_contract,
                    ))),
                    related_paths=fact_related_paths,
                ))
                related_by_path.setdefault(source.path, set()).update(
                    fact_related_paths
                )

            for (
                line_number,
                base_target_name,
                via_methods,
                target_method,
                caller,
                base_resolution,
            ) in sorted(chained_relations):
                resolved = self.resolve_chained_instance_call(
                    source,
                    base_target_name,
                    via_methods,
                    target_method,
                    caller,
                    base_resolution,
                    line_number,
                    symbols_by_name,
                )
                if resolved is None:
                    continue
                fact, fact_related_paths = resolved
                facts_by_path.setdefault(source.path, set()).add(fact)
                related_by_path.setdefault(source.path, set()).update(
                    fact_related_paths
                )

        return tuple(
            ArchitecturePacket(
                plugin_id=self.plugin_id,
                kind="php-code-relation",
                key=source_path,
                paths=tuple(sorted({
                    source_path,
                    *related_by_path.get(source_path, ()),
                })),
                facts=tuple(sorted(facts)),
                attributes=(("resolution", "unique-repository-symbol"),),
            )
            for source_path, facts in sorted(facts_by_path.items())
            if facts
        )

    def resolve_chained_instance_call(
        self,
        source: SymbolDefinition,
        base_target_name: str,
        via_methods: tuple[str, ...],
        target_method: str,
        caller: str,
        base_resolution: str,
        line_number: int,
        symbols_by_name: dict[str, list[SymbolDefinition]],
    ) -> tuple[GraphFact, tuple[str, ...]] | None:
        """Follow only exact, non-null, in-repository declared return types."""
        base_candidates = symbols_by_name.get(base_target_name.casefold(), ())
        if len(base_candidates) != 1:
            return None
        current = base_candidates[0]
        related_paths: set[str] = {current.path}
        chain_attributes: list[tuple[str, str]] = []

        for index, method_name in enumerate(via_methods):
            contract, declaration_path = self.declared_method_contract(
                current,
                method_name,
                symbols_by_name,
            )
            contract_values = dict(contract)
            declared_return_type = contract_values.get(
                "targetDeclaredReturnType",
                "",
            )
            return_target = self.exact_declared_return_symbol(
                declared_return_type,
                contract_values,
                symbols_by_name,
            )
            if return_target is None:
                return None
            if declaration_path:
                related_paths.add(declaration_path)
            related_paths.add(return_target.path)
            prefix = f"receiverCall:{index:04d}:"
            chain_attributes.extend((
                (f"{prefix}sourceType", current.qualified_name),
                (f"{prefix}method", method_name),
                (f"{prefix}declaredReturnType", declared_return_type),
                (
                    f"{prefix}methodDeclaredOn",
                    contract_values["targetMethodDeclaredOn"],
                ),
            ))
            current = return_target

        if current.path == source.path:
            return None
        target_contract, target_declaration_path = self.declared_method_contract(
            current,
            target_method,
            symbols_by_name,
        )
        if target_declaration_path:
            related_paths.add(target_declaration_path)
        relation_attributes = (
            *((("callerMethod", caller),) if caller else ()),
            ("receiverBaseResolution", base_resolution),
            ("receiverResolution", "exact-call-return"),
            ("retrievalIdentifier:targetMethod", target_method),
            ("targetMethod", target_method),
            *chain_attributes,
            *target_contract,
        )
        fact_related_paths = tuple(sorted(related_paths))
        return (
            GraphFact(
                kind="php-instance-call-relation",
                source=source.qualified_name,
                relation="calls-instance",
                target=current.qualified_name,
                path=source.path,
                line=line_number,
                attributes=tuple(sorted(relation_attributes)),
                related_paths=fact_related_paths,
            ),
            fact_related_paths,
        )

    @staticmethod
    def exact_declared_return_symbol(
        declared_return_type: str,
        method_contract: dict[str, str],
        symbols_by_name: dict[str, list[SymbolDefinition]],
    ) -> SymbolDefinition | None:
        """Accept one concrete named type, never nullable/union/intersection/builtin."""
        if declared_return_type.casefold() == "self":
            declaring_name = method_contract.get("targetMethodDeclaredOn", "")
            candidates = symbols_by_name.get(declaring_name.casefold(), ())
            return candidates[0] if len(candidates) == 1 else None
        if (
            not declared_return_type
            or declared_return_type.casefold() in _BUILTIN_TYPES
            or re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_]*(?:\\[A-Za-z_][A-Za-z0-9_]*)*",
                declared_return_type,
            )
            is None
        ):
            return None
        candidates = symbols_by_name.get(declared_return_type.casefold(), ())
        return candidates[0] if len(candidates) == 1 else None

    def declared_method_contract(
        self,
        target: SymbolDefinition,
        method_name: str,
        symbols_by_name: dict[str, list[SymbolDefinition]],
    ) -> tuple[tuple[tuple[str, str], ...], str]:
        """Project an unambiguous direct or parent-declared target method.

        Parent traversal requires one exact in-repository class at every step.
        Trait, interface-contract, magic, external, ambiguous, and private-parent
        cases remain unknown rather than being turned into absence assertions.
        """
        if not method_name:
            return (), ""

        declaring_target = target
        declaration_origin = "direct"
        seen: set[str] = set()
        while True:
            identity = declaring_target.qualified_name.casefold()
            if identity in seen:
                return (), ""
            seen.add(identity)
            declared = tuple(
                candidate
                for candidate in declaring_target.methods
                if candidate.casefold() == method_name.casefold()
            )
            if len(declared) == 1:
                break
            if declared:
                return (), ""
            parent_name = dict(declaring_target.attributes).get(
                "php-parent-class",
                "",
            )
            if not parent_name:
                return (), ""
            parent_candidates = symbols_by_name.get(
                parent_name.casefold(),
                (),
            )
            if len(parent_candidates) != 1:
                return (), ""
            declaring_target = parent_candidates[0]
            declaration_origin = "inherited-parent"

        exact_name = declared[0]
        prefix = f"method:{exact_name}:"
        target_attributes = {
            key[len(prefix):]: value
            for key, value in declaring_target.attributes
            if key.startswith(prefix)
        }
        if (
            declaration_origin == "inherited-parent"
            and target_attributes.get("visibility") == "private"
        ):
            return (), ""
        contract = {
            "targetMethodDeclared": "true",
            "targetMethodDeclarationOrigin": declaration_origin,
            "targetMethodDeclaredOn": declaring_target.qualified_name,
        }
        projected = {
            "final": "targetMethodFinal",
            "returnType": "targetDeclaredReturnType",
            "static": "targetMethodStatic",
            "visibility": "targetMethodVisibility",
        }
        for source_key, fact_key in projected.items():
            value = target_attributes.get(source_key)
            if value:
                contract[fact_key] = value
        return (
            tuple(sorted(contract.items())),
            declaring_target.path,
        )
