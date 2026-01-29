; TypeScript tree-sitter queries for AST-based code splitting
; Uses same patterns as JavaScript plus TypeScript-specific nodes

; Import statements
(import_statement) @import

; Export statements
(export_statement) @export

; Class declarations
(class_declaration
  name: (type_identifier) @name) @definition.class

; Abstract class declarations
(abstract_class_declaration
  name: (type_identifier) @name) @definition.class

; Function declarations
(function_declaration
  name: (identifier) @name) @definition.function

; Arrow functions assigned to variables
(lexical_declaration
  (variable_declarator
    name: (identifier) @name
    value: (arrow_function))) @definition.function

; Method definitions (inside class body)
(method_definition
  name: (property_identifier) @name) @definition.method

; Interface declarations
(interface_declaration
  name: (type_identifier) @name) @definition.interface

; Type alias declarations
(type_alias_declaration
  name: (type_identifier) @name) @definition.type

; Enum declarations
(enum_declaration
  name: (identifier) @name) @definition.enum

; Module declarations
(module
  name: (identifier) @name) @definition.module

; Variable declarations (module-level)
(lexical_declaration) @definition.variable

; Ambient declarations
(ambient_declaration) @definition.ambient
