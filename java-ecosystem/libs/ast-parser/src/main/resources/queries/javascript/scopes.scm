; JavaScript scope queries for tree-sitter
; Captures: @function.def, @class.def, @block.def, @name

; ── Functions ────────────────────────────────────────────────────────
(function_declaration
  name: (identifier) @name) @function.def

(method_definition
  name: (property_identifier) @name) @function.def

(arrow_function) @function.def

(function) @function.def

(generator_function_declaration
  name: (identifier) @name) @function.def

; ── Classes ──────────────────────────────────────────────────────────
(class_declaration
  name: (identifier) @name) @class.def

(class) @class.def

; ── Control-flow blocks ──────────────────────────────────────────────
(if_statement) @block.def
(for_statement) @block.def
(for_in_statement) @block.def
(while_statement) @block.def
(do_statement) @block.def
(try_statement) @block.def
(catch_clause) @block.def
(finally_clause) @block.def
(switch_statement) @block.def
