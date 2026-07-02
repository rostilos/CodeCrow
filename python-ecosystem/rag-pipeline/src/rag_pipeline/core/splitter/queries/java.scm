; Java tree-sitter queries for AST-based code splitting

; Package declaration
(package_declaration
  (scoped_identifier) @package.name) @package

; Import declarations
(import_declaration
  (scoped_identifier) @import.path) @import

; Class declarations
(class_declaration
  name: (identifier) @name) @definition.class

(class_declaration
  name: (identifier) @name
  superclass: (superclass
    (type_identifier) @class.extends)) @definition.class

(class_declaration
  name: (identifier) @name
  interfaces: (super_interfaces
    (type_list) @class.implements)) @definition.class

; Interface declarations
(interface_declaration
  name: (identifier) @name) @definition.interface

; Enum declarations
(enum_declaration
  name: (identifier) @name) @definition.enum

; Record declarations (Java 14+)
(record_declaration
  name: (identifier) @name) @definition.record

; Annotation type declarations
(annotation_type_declaration
  name: (identifier) @name) @definition.annotation

; Method declarations
(method_declaration
  type: (_) @method.return_type
  name: (identifier) @name) @definition.method

; Constructor declarations
(constructor_declaration
  name: (identifier) @name) @definition.constructor

; Field declarations
(field_declaration
  type: (_) @field.type
  declarator: (variable_declarator
    name: (identifier) @name)) @definition.field

; Parameters
(formal_parameter
  type: (_) @parameter.type
  name: (identifier) @parameter.name) @parameter

; Local variables
(local_variable_declaration
  type: (_) @variable.type
  declarator: (variable_declarator
    name: (identifier) @variable.name)) @variable

; Method calls
(method_invocation
  object: (_) @call.object
  name: (identifier) @call.name) @call

(method_invocation
  name: (identifier) @call.name) @call

; Constructor calls and type references
(object_creation_expression
  type: (_) @type_reference.name) @type_reference

; Annotations (for metadata) - simple names
(marker_annotation
  name: (identifier) @name) @annotation

(annotation
  name: (identifier) @name) @annotation

; Annotations with fully qualified names (e.g., @org.junit.Test)
(marker_annotation
  name: (scoped_identifier) @name) @annotation

(annotation
  name: (scoped_identifier) @name) @annotation
