; JavaScript/TypeScript tree-sitter queries for AST-based code splitting

; Import statements
(import_statement) @import

; Export statements
(export_statement) @export

; Class declarations
(class_declaration
  name: (identifier) @name) @definition.class

; Function declarations
(function_declaration
  name: (identifier) @name) @definition.function

; Arrow functions assigned to variables
(lexical_declaration
  (variable_declarator
    name: (identifier) @name
    value: (arrow_function))) @definition.function

; Generator functions
(generator_function_declaration
  name: (identifier) @name) @definition.function

; Method definitions (inside class body)
(method_definition
  name: (property_identifier) @name) @definition.method

; Variable declarations (module-level)
(lexical_declaration) @definition.variable

(variable_declaration) @definition.variable

; Interface declarations (TypeScript)
(interface_declaration
  name: (type_identifier) @name) @definition.interface

; Type alias declarations (TypeScript)
(type_alias_declaration
  name: (type_identifier) @name) @definition.type
