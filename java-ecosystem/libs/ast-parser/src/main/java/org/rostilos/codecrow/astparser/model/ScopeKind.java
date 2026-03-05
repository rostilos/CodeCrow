package org.rostilos.codecrow.astparser.model;

/**
 * Categorizes the kind of syntactic scope in source code.
 * <p>
 * Each scope kind maps to tree-sitter node types through per-language
 * S-expression queries in {@code queries/<lang>/scopes.scm}.
 * <p>
 * The hierarchy from narrow to broad is:
 * BLOCK → FUNCTION → CLASS → NAMESPACE → MODULE
 */
public enum ScopeKind {

    /** A control-flow block: if/else, for, while, try/catch, switch-case, etc. */
    BLOCK("Block", 1),

    /** A function, method, lambda, or closure. */
    FUNCTION("Function", 2),

    /** A class, struct, trait, interface, or enum declaration. */
    CLASS("Class", 3),

    /** A namespace, package declaration, or module scope within a file. */
    NAMESPACE("Namespace", 4),

    /** The top-level file/module scope (root node in the AST). */
    MODULE("Module", 5);

    private final String displayName;

    /**
     * Nesting depth indicator: higher = broader scope.
     * Useful for ordering scopes from innermost to outermost.
     */
    private final int breadth;

    ScopeKind(String displayName, int breadth) {
        this.displayName = displayName;
        this.breadth = breadth;
    }

    public String getDisplayName() {
        return displayName;
    }

    public int getBreadth() {
        return breadth;
    }
}
