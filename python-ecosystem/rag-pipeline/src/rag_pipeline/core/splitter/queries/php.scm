; PHP tree-sitter queries for AST-based code splitting

; Namespace definition
(namespace_definition) @namespace

; Use statements
(namespace_use_declaration) @use

; Class declarations
(class_declaration
  name: (name) @name) @definition.class

; Interface declarations
(interface_declaration
  name: (name) @name) @definition.interface

; Trait declarations
(trait_declaration
  name: (name) @name) @definition.trait

; Enum declarations (PHP 8.1+)
(enum_declaration
  name: (name) @name) @definition.enum

; Function definitions
(function_definition
  name: (name) @name) @definition.function

; Method declarations
(method_declaration
  name: (name) @name) @definition.method

; Property declarations
(property_declaration) @definition.property

; Const declarations
(const_declaration) @definition.const

; Attributes (PHP 8.0+)
(attribute) @attribute
