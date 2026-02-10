; Java tree-sitter queries for AST-based code splitting

; Package declaration
(package_declaration) @package

; Import declarations  
(import_declaration) @import

; Class declarations
(class_declaration
  name: (identifier) @name) @definition.class

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
  name: (identifier) @name) @definition.method

; Constructor declarations
(constructor_declaration
  name: (identifier) @name) @definition.constructor

; Field declarations
(field_declaration) @definition.field

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
