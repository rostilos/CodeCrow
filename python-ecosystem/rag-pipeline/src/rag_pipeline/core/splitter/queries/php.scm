; PHP tree-sitter queries for AST-based code splitting

; Namespace definition
(namespace_definition
  (namespace_name) @namespace.name) @namespace

; Use statements
(namespace_use_declaration
  (namespace_use_clause
    (qualified_name) @use.path)) @use

; Class declarations
(class_declaration
  name: (name) @name) @definition.class

(class_declaration
  name: (name) @name
  (base_clause
    (name) @class.extends)) @definition.class

(class_declaration
  name: (name) @name
  (class_interface_clause
    (name) @class.implements)) @definition.class

; Function definitions
(function_definition
  (name) @name) @definition.function

; Method declarations
(method_declaration
  (name) @name) @definition.method

(method_declaration
  return_type: (_) @method.return_type) @definition.method

; Property declarations
(property_declaration
  type: (_) @field.type
  (property_element
    (variable_name
      (name) @name))) @definition.field

; Parameters
(simple_parameter
  type: (_) @parameter.type
  name: (variable_name
    (name) @parameter.name)) @parameter

; Instance method calls
(member_call_expression
  object: (_) @call.object
  name: (name) @call.name) @call

; Static method calls
(scoped_call_expression
  scope: (_) @call.object
  name: (name) @call.name) @call

; Constructor calls and type references
(object_creation_expression
  (name) @type_reference.name) @type_reference

; Const declarations
(const_declaration) @definition.const

; Attributes (PHP 8.0+)
(attribute) @attribute
