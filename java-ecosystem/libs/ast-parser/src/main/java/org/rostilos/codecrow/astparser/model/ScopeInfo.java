package org.rostilos.codecrow.astparser.model;

/**
 * A resolved syntactic scope from the AST.
 * <p>
 * Represents a named region of code (function, class, block, etc.)
 * with its exact start and end line numbers as determined by the parser.
 * <p>
 * Line numbers are 1-based and inclusive on both ends,
 * matching the convention used throughout the platform.
 *
 * @param kind      the category of this scope (FUNCTION, CLASS, BLOCK, etc.)
 * @param name      the identifier/name of the scope (e.g. method name, class name).
 *                  May be empty for anonymous blocks (e.g. if/else, lambdas).
 * @param startLine 1-based inclusive start line
 * @param endLine   1-based inclusive end line
 * @param parentIndex index of the parent scope in the same scope list, or -1 if top-level.
 *                    Enables building scope trees without separate tree data structures.
 */
public record ScopeInfo(
        ScopeKind kind,
        String name,
        int startLine,
        int endLine,
        int parentIndex
) {
    /** Convenience constructor for scopes without a known parent. */
    public ScopeInfo(ScopeKind kind, String name, int startLine, int endLine) {
        this(kind, name, startLine, endLine, -1);
    }

    /** Number of lines this scope spans (inclusive). */
    public int lineSpan() {
        return endLine - startLine + 1;
    }

    /** Check if a given 1-based line number falls within this scope. */
    public boolean containsLine(int line) {
        return line >= startLine && line <= endLine;
    }
}
