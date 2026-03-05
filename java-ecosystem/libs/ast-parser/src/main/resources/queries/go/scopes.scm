; Go scope queries for tree-sitter
; Captures: @function.def, @class.def, @block.def, @namespace.def, @name

; ── Functions & Methods ──────────────────────────────────────────────
(function_declaration
  name: (identifier) @name) @function.def

(method_declaration
  name: (field_identifier) @name) @function.def

(func_literal) @function.def

; ── Types (Go's equivalent of classes) ───────────────────────────────
(type_declaration
  (type_spec
    name: (type_identifier) @name)) @class.def

; ── Control-flow blocks ──────────────────────────────────────────────
(if_statement) @block.def
(for_statement) @block.def
(select_statement) @block.def
(type_switch_statement) @block.def
(expression_switch_statement) @block.def

; ── Package declaration ──────────────────────────────────────────────
(package_clause
  (package_identifier) @name) @namespace.def
