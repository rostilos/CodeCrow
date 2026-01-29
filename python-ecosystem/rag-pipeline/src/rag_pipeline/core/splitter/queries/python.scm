; Python tree-sitter queries for AST-based code splitting

; Import statements
(import_statement) @import

(import_from_statement) @import

; Class definitions
(class_definition
  name: (identifier) @name) @definition.class

; Function definitions
(function_definition
  name: (identifier) @name) @definition.function

; Decorated definitions (class or function with decorators)
(decorated_definition) @definition.decorated

; Decorators
(decorator) @decorator

; Assignment statements (module-level constants)
(assignment
  left: (identifier) @name) @definition.assignment

; Type alias (Python 3.12+)
(type_alias_statement
  name: (type) @name) @definition.type_alias
