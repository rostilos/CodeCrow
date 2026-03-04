; PHP scope queries for tree-sitter
; Captures: @function.def, @class.def, @block.def, @namespace.def, @name

; ── Functions & Methods ──────────────────────────────────────────────
(function_definition
  name: (name) @name) @function.def

(method_declaration
  name: (name) @name) @function.def

(anonymous_function) @function.def

(arrow_function) @function.def

; ── Classes, Interfaces, Traits, Enums ───────────────────────────────
(class_declaration
  name: (name) @name) @class.def

(interface_declaration
  name: (name) @name) @class.def

(trait_declaration
  name: (name) @name) @class.def

(enum_declaration
  name: (name) @name) @class.def

; ── Control-flow blocks ──────────────────────────────────────────────
(if_statement) @block.def
(for_statement) @block.def
(foreach_statement) @block.def
(while_statement) @block.def
(do_statement) @block.def
(try_statement) @block.def
(catch_clause) @block.def
(finally_clause) @block.def
(switch_statement) @block.def

; ── Namespaces ───────────────────────────────────────────────────────
(namespace_definition
  name: (namespace_name) @name) @namespace.def
