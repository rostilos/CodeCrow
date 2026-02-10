; Go tree-sitter queries for AST-based code splitting

; Package clause
(package_clause) @package

; Import declarations
(import_declaration) @import

; Type declarations (struct, interface, type alias)
(type_declaration
  (type_spec
    name: (type_identifier) @name)) @definition.type

; Function declarations
(function_declaration
  name: (identifier) @name) @definition.function

; Method declarations
(method_declaration
  name: (field_identifier) @name) @definition.method

; Variable declarations
(var_declaration) @definition.variable

; Constant declarations
(const_declaration) @definition.const
