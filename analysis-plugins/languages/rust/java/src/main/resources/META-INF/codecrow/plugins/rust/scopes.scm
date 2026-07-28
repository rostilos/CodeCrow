; Rust scope queries for tree-sitter
; Captures: @function.def, @class.def, @block.def, @namespace.def, @name

; ── Functions ────────────────────────────────────────────────────────
(function_item
  name: (identifier) @name) @function.def

(closure_expression) @function.def

; ── Structs, Enums, Traits, Impls ────────────────────────────────────
(struct_item
  name: (type_identifier) @name) @class.def

(enum_item
  name: (type_identifier) @name) @class.def

(trait_item
  name: (type_identifier) @name) @class.def

(impl_item) @class.def

(type_item
  name: (type_identifier) @name) @class.def

; ── Control-flow blocks ──────────────────────────────────────────────
(if_expression) @block.def
(for_expression) @block.def
(while_expression) @block.def
(loop_expression) @block.def
(match_expression) @block.def

; ── Modules ──────────────────────────────────────────────────────────
(mod_item
  name: (identifier) @name) @namespace.def
