; CodeCrow PHP language plugin scope queries
; Captures: @function.def, @class.def, @block.def, @namespace.def, @name

(function_definition
  name: (name) @name) @function.def

(method_declaration
  name: (name) @name) @function.def

(anonymous_function) @function.def
(arrow_function) @function.def

(class_declaration
  name: (name) @name) @class.def

(interface_declaration
  name: (name) @name) @class.def

(trait_declaration
  name: (name) @name) @class.def

(enum_declaration
  name: (name) @name) @class.def

(if_statement) @block.def
(for_statement) @block.def
(foreach_statement) @block.def
(while_statement) @block.def
(do_statement) @block.def
(try_statement) @block.def
(catch_clause) @block.def
(finally_clause) @block.def
(switch_statement) @block.def

(namespace_definition
  name: (namespace_name) @name) @namespace.def
