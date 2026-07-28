; Bash scope queries for tree-sitter
; Captures: @function.def, @block.def, @name

; ── Functions ────────────────────────────────────────────────────────
(function_definition
  name: (word) @name) @function.def

; ── Control-flow blocks ──────────────────────────────────────────────
(if_statement) @block.def
(for_statement) @block.def
(while_statement) @block.def
(case_statement) @block.def
(subshell) @block.def
