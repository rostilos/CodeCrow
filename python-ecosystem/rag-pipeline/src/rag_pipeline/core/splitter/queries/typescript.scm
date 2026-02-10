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

; Module declarations (ambient/string-named modules)
(module
  name: (identifier) @name) @definition.module

; TypeScript namespace declarations (internal modules)
(internal_module
  name: (identifier) @name) @definition.namespace

; Variable declarations at module level (excluding arrow functions captured above)
; Only capture if NOT an arrow function assignment
(lexical_declaration
  (variable_declarator
    name: (identifier) @name
    value: (_) @value) @declarator
  (#not-match? @value "^arrow_function")) @definition.variable

; Ambient declarations
(ambient_declaration) @definition.ambient
