; C scope queries for tree-sitter
; Captures: @function.def, @class.def, @block.def, @name

; ── Functions ────────────────────────────────────────────────────────
(function_definition
  declarator: (function_declarator
    declarator: (identifier) @name)) @function.def

; ── Structs & Enums ──────────────────────────────────────────────────
(struct_specifier
  name: (type_identifier) @name) @class.def

(enum_specifier
  name: (type_identifier) @name) @class.def

(union_specifier
  name: (type_identifier) @name) @class.def

; ── Control-flow blocks ──────────────────────────────────────────────
(if_statement) @block.def
(for_statement) @block.def
(while_statement) @block.def
(do_statement) @block.def
(switch_statement) @block.def
