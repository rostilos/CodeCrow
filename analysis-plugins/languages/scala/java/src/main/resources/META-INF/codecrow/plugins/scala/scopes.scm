; Scala scope queries for tree-sitter
; Captures: @function.def, @class.def, @block.def, @namespace.def, @name

; ── Functions & Methods ──────────────────────────────────────────────
(function_definition
  name: (identifier) @name) @function.def

(lambda_expression) @function.def

; ── Classes, Objects, Traits ─────────────────────────────────────────
(class_definition
  name: (identifier) @name) @class.def

(object_definition
  name: (identifier) @name) @class.def

(trait_definition
  name: (identifier) @name) @class.def

; ── Control-flow blocks ──────────────────────────────────────────────
(if_expression) @block.def
(for_expression) @block.def
(while_expression) @block.def
(try_expression) @block.def
(match_expression) @block.def

; ── Packages ─────────────────────────────────────────────────────────
(package_clause) @namespace.def
