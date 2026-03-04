package org.rostilos.codecrow.astparser.api;

/**
 * Thrown when AST parsing fails in an unrecoverable way.
 * <p>
 * Note: tree-sitter itself is error-tolerant and always produces a tree even
 * for syntactically broken code. This exception is for infrastructure failures
 * (grammar not loaded, JNI errors, pool exhausted, etc.).
 */
public class AstParseException extends RuntimeException {

    public AstParseException(String message) {
        super(message);
    }

    public AstParseException(String message, Throwable cause) {
        super(message, cause);
    }
}
