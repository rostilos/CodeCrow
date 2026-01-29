; C# tree-sitter queries for AST-based code splitting

; Using directives
(using_directive) @using

; Namespace declarations
(namespace_declaration
  name: (identifier) @name) @definition.namespace

(file_scoped_namespace_declaration
  name: (identifier) @name) @definition.namespace

; Class declarations
(class_declaration
  name: (identifier) @name) @definition.class

; Struct declarations
(struct_declaration
  name: (identifier) @name) @definition.struct

; Interface declarations
(interface_declaration
  name: (identifier) @name) @definition.interface

; Enum declarations
(enum_declaration
  name: (identifier) @name) @definition.enum

; Record declarations
(record_declaration
  name: (identifier) @name) @definition.record

; Delegate declarations
(delegate_declaration
  name: (identifier) @name) @definition.delegate

; Method declarations
(method_declaration
  name: (identifier) @name) @definition.method

; Constructor declarations
(constructor_declaration
  name: (identifier) @name) @definition.constructor

; Property declarations
(property_declaration
  name: (identifier) @name) @definition.property

; Field declarations
(field_declaration) @definition.field

; Event declarations
(event_declaration) @definition.event

; Attributes
(attribute) @attribute
