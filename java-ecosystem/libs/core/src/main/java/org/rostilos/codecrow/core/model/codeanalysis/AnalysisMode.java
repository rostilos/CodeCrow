package org.rostilos.codecrow.core.model.codeanalysis;

/**
 * Defines the analysis mode for PR code reviews.
 * 
 * FULL - Analyzes the complete PR diff (source branch vs target branch)
 * INCREMENTAL - Analyzes only the delta diff (changes since last analyzed commit)
 */
public enum AnalysisMode {
    /**
     * Full PR analysis - analyzes entire diff from source to target branch.
     * Used for:
     * - First review of a PR
     * - When delta is too large (>50% of files changed)
     * - When no previous analysis exists
     */
    FULL,
    
    /**
     * Incremental analysis - analyzes only changes since last review.
     * Used for:
     * - Subsequent PR updates
     * - When previous analysis exists with a different commit
     * Benefits:
     * - Reduced context size
     * - Faster analysis
     * - Focus on new/changed code
     */
    INCREMENTAL
}
