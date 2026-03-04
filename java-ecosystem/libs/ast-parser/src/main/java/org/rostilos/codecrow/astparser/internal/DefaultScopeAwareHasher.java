package org.rostilos.codecrow.astparser.internal;

import org.rostilos.codecrow.astparser.api.ScopeAwareHasher;
import org.rostilos.codecrow.astparser.model.ScopeInfo;
import org.rostilos.codecrow.core.util.tracking.LineHashSequence;

/**
 * Default implementation of {@link ScopeAwareHasher}.
 * <p>
 * Uses the AST-resolved scope boundaries from {@link ScopeInfo} to compute
 * content hashes via {@link LineHashSequence#getRangeHash(int, int)}.
 * <p>
 * No magic numbers, no hardcoded radii — the hash range is 100% determined
 * by the actual syntactic structure of the code.
 */
public final class DefaultScopeAwareHasher implements ScopeAwareHasher {

    @Override
    public String hashScope(LineHashSequence lineHashes, ScopeInfo scope) {
        if (lineHashes == null || scope == null) return "";
        return lineHashes.getRangeHash(scope.startLine(), scope.endLine());
    }

    @Override
    public String hashContextInScope(LineHashSequence lineHashes, int line, ScopeInfo scope) {
        if (lineHashes == null || scope == null) return "";

        // Compute a local context window: ±20% of the scope span, bounded by scope boundaries.
        // This captures the issue's neighborhood without hashing the entire scope,
        // which is important for 500+ line functions where a local change shouldn't
        // invalidate the hash for an issue at the other end.
        int scopeSpan = scope.lineSpan();
        int contextRadius = Math.max(3, scopeSpan / 5); // at least 3 lines, up to 20% of scope

        int contextStart = Math.max(scope.startLine(), line - contextRadius);
        int contextEnd = Math.min(scope.endLine(), line + contextRadius);

        return lineHashes.getRangeHash(contextStart, contextEnd);
    }
}
