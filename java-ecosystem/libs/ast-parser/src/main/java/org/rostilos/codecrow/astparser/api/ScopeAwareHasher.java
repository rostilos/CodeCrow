package org.rostilos.codecrow.astparser.api;

import org.rostilos.codecrow.astparser.model.ScopeInfo;
import org.rostilos.codecrow.core.util.tracking.LineHashSequence;

/**
 * Computes content hashes scoped to AST-resolved boundaries.
 * <p>
 * Instead of using hardcoded radii or snippet-length guesses, this hasher
 * uses the exact scope boundaries from the AST to compute hashes that
 * accurately track changes within a function, class, or block.
 * <p>
 * The hash covers the entire scope body, making it robust against:
 * <ul>
 *   <li>Line insertions/deletions elsewhere in the file (no drift)</li>
 *   <li>Enterprise-scale functions (hundreds of lines) — hash covers the real range</li>
 *   <li>Nested scopes — innermost scope used for precision</li>
 * </ul>
 */
public interface ScopeAwareHasher {

    /**
     * Compute a content hash for the scope that contains the given line.
     *
     * @param lineHashes the line hash sequence for the file
     * @param scope      the AST-resolved scope to hash
     * @return hex-encoded hash of the scope's content lines
     */
    String hashScope(LineHashSequence lineHashes, ScopeInfo scope);

    /**
     * Compute a context hash for a specific line within its enclosing scope.
     * <p>
     * Unlike the full scope hash, this hashes only the lines around the target line
     * up to the scope boundaries. This is useful for detecting local changes near an
     * issue while ignoring changes at the other end of a large function.
     *
     * @param lineHashes the line hash sequence for the file
     * @param line       1-based line number of the issue
     * @param scope      the enclosing scope (provides boundaries)
     * @return hex-encoded context hash
     */
    String hashContextInScope(LineHashSequence lineHashes, int line, ScopeInfo scope);
}
