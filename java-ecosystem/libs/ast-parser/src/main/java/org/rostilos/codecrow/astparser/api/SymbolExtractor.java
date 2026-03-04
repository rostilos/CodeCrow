package org.rostilos.codecrow.astparser.api;

import org.rostilos.codecrow.astparser.model.ParsedTree;
import org.rostilos.codecrow.astparser.model.SymbolInfo;

/**
 * Extracts symbol information from a parsed AST.
 * <p>
 * Provides the same data that the Python RAG pipeline's {@code /parse} endpoint
 * returns (imports, classes, functions, calls, namespace, parentClass), but
 * computed on the Java side for zero network overhead and consistency.
 * <p>
 * Implementations must be thread-safe.
 */
public interface SymbolExtractor {

    /**
     * Extract all symbols from the parsed tree.
     *
     * @param parsedTree the parsed AST
     * @return symbol information, never null. Returns {@link SymbolInfo#empty()} on failure.
     */
    SymbolInfo extract(ParsedTree parsedTree);
}
