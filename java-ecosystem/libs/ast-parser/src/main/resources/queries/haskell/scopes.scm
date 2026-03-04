; Haskell scope queries for tree-sitter
; Captures: @function.def, @class.def, @namespace.def, @name

; ── Functions ────────────────────────────────────────────────────────
(function
  name: (variable) @name) @function.def

; ── Type classes and data declarations ───────────────────────────────
(class
  name: (class_head) @name) @class.def

(data_type
  name: (type) @name) @class.def

(newtype
  name: (type) @name) @class.def

(instance
  name: (instance_head) @name) @class.def

; ── Module ───────────────────────────────────────────────────────────
(module) @namespace.def
