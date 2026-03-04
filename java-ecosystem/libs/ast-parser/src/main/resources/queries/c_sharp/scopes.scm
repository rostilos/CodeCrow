; C# scope queries for tree-sitter
; Captures: @function.def, @class.def, @block.def, @namespace.def, @name

; ── Methods & Functions ──────────────────────────────────────────────
(method_declaration
  name: (identifier) @name) @function.def

(constructor_declaration
  name: (identifier) @name) @function.def

(lambda_expression) @function.def

(local_function_statement
  name: (identifier) @name) @function.def

; ── Classes, Structs, Interfaces, Enums ──────────────────────────────
(class_declaration
  name: (identifier) @name) @class.def

(struct_declaration
  name: (identifier) @name) @class.def

(interface_declaration
  name: (identifier) @name) @class.def

(enum_declaration
  name: (identifier) @name) @class.def

(record_declaration
  name: (identifier) @name) @class.def

; ── Control-flow blocks ──────────────────────────────────────────────
(if_statement) @block.def
(for_statement) @block.def
(for_each_statement) @block.def
(while_statement) @block.def
(do_statement) @block.def
(try_statement) @block.def
(catch_clause) @block.def
(finally_clause) @block.def
(switch_statement) @block.def
(switch_expression) @block.def

; ── Namespaces ───────────────────────────────────────────────────────
(namespace_declaration
  name: (identifier) @name) @namespace.def

(file_scoped_namespace_declaration
  name: (identifier) @name) @namespace.def
