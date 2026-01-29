; Rust tree-sitter queries for AST-based code splitting

; Use declarations
(use_declaration) @use

; Module declarations
(mod_item
  name: (identifier) @name) @definition.module

; Struct definitions
(struct_item
  name: (type_identifier) @name) @definition.struct

; Enum definitions
(enum_item
  name: (type_identifier) @name) @definition.enum

; Trait definitions
(trait_item
  name: (type_identifier) @name) @definition.trait

; Implementation blocks
(impl_item) @definition.impl

; Function definitions
(function_item
  name: (identifier) @name) @definition.function

; Type alias
(type_item
  name: (type_identifier) @name) @definition.type

; Constant definitions
(const_item
  name: (identifier) @name) @definition.const

; Static definitions
(static_item
  name: (identifier) @name) @definition.static

; Macro definitions
(macro_definition
  name: (identifier) @name) @definition.macro

; Attributes
(attribute_item) @attribute
