; Ruby scope queries for tree-sitter
; Captures: @function.def, @class.def, @block.def, @namespace.def, @name

; ── Methods ──────────────────────────────────────────────────────────
(method
  name: (identifier) @name) @function.def

(singleton_method
  name: (identifier) @name) @function.def

(lambda) @function.def

(block) @function.def

(do_block) @function.def

; ── Classes & Modules ────────────────────────────────────────────────
(class
  name: (constant) @name) @class.def

(singleton_class) @class.def

(module
  name: (constant) @name) @namespace.def

; ── Control-flow blocks ──────────────────────────────────────────────
(if) @block.def
(unless) @block.def
(while) @block.def
(until) @block.def
(for) @block.def
(begin) @block.def
(case) @block.def
