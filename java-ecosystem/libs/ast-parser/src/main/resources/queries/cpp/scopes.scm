; C++ scope queries for tree-sitter
; Captures: @function.def, @class.def, @block.def, @namespace.def, @name

; ── Functions & Methods ──────────────────────────────────────────────
(function_definition
  declarator: (function_declarator
    declarator: (identifier) @name)) @function.def

(function_definition
  declarator: (function_declarator
    declarator: (qualified_identifier) @name)) @function.def

(lambda_expression) @function.def

; ── Classes, Structs, Enums ──────────────────────────────────────────
(class_specifier
  name: (type_identifier) @name) @class.def

(struct_specifier
  name: (type_identifier) @name) @class.def

(enum_specifier
  name: (type_identifier) @name) @class.def

(union_specifier
  name: (type_identifier) @name) @class.def

; ── Control-flow blocks ──────────────────────────────────────────────
(if_statement) @block.def
(for_statement) @block.def
(for_range_loop) @block.def
(while_statement) @block.def
(do_statement) @block.def
(switch_statement) @block.def
(try_statement) @block.def
(catch_clause) @block.def

; ── Namespaces ───────────────────────────────────────────────────────
(namespace_definition
  name: (identifier) @name) @namespace.def
