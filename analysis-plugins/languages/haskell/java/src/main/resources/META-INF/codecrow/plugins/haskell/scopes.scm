; Haskell scope queries for tree-sitter
; Captures: @function.def, @class.def, @namespace.def, @name

; ── Functions ────────────────────────────────────────────────────────
(function
  name: (variable) @name) @function.def

; ── Type classes and data declarations ───────────────────────────────
(class
  name: (name) @name) @class.def

(data_type
  name: (name) @name) @class.def

(newtype
  name: (name) @name) @class.def

(instance
  name: (name) @name) @class.def

; ── Module ───────────────────────────────────────────────────────────
(module) @namespace.def
