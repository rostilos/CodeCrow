; Python scope queries for tree-sitter
; Captures: @function.def, @class.def, @block.def, @namespace.def, @name

; ── Functions & Methods ──────────────────────────────────────────────
(function_definition
  name: (identifier) @name) @function.def

(lambda) @function.def

; ── Classes ──────────────────────────────────────────────────────────
(class_definition
  name: (identifier) @name) @class.def

; ── Control-flow blocks ──────────────────────────────────────────────
(if_statement) @block.def
(for_statement) @block.def
(while_statement) @block.def
(try_statement) @block.def
(except_clause) @block.def
(with_statement) @block.def
(match_statement) @block.def
(case_clause) @block.def
