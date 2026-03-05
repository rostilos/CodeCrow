package org.rostilos.codecrow.astparser.api;

import org.rostilos.codecrow.astparser.model.ParsedTree;
import org.rostilos.codecrow.astparser.model.ScopeInfo;

import java.util.List;
import java.util.Optional;

/**
 * Resolves syntactic scopes from a parsed AST.
 * <p>
 * Given a parsed tree, extracts all named scopes (functions, classes, blocks, namespaces)
 * with their exact line boundaries. This is the core contract that replaces
 * regex-based heuristics and hardcoded radii with deterministic AST data.
 * <p>
 * Implementations must be thread-safe (a parsed tree is immutable).
 */
public interface ScopeResolver {

    /**
     * Extract all scopes from the parsed tree, ordered by start line.
     * <p>
     * The returned list includes nested scopes. Use {@link ScopeInfo#parentIndex()}
     * to reconstruct the nesting hierarchy.
     *
     * @param parsedTree the parsed AST
     * @return ordered list of all scopes found, never null (may be empty)
     */
    List<ScopeInfo> resolveAll(ParsedTree parsedTree);

    /**
     * Find the innermost scope that contains the given line.
     * <p>
     * If multiple scopes contain the line (nested function inside a class),
     * returns the narrowest (innermost) one.
     *
     * @param parsedTree the parsed AST
     * @param line       1-based line number
     * @return the innermost scope, or empty if the line is at module/file level
     */
    Optional<ScopeInfo> innermostScopeAt(ParsedTree parsedTree, int line);

    /**
     * Find all scopes that contain the given line, ordered from innermost to outermost.
     *
     * @param parsedTree the parsed AST
     * @param line       1-based line number
     * @return scopes containing the line, innermost first. Never null.
     */
    List<ScopeInfo> scopeChainAt(ParsedTree parsedTree, int line);
}
