package org.rostilos.codecrow.core.model.project.config;

/**
 * Selects the PR review engine while keeping the surrounding acquisition and
 * delivery pipeline unchanged.
 */
public enum ReviewApproach {
    CLASSIC,
    AGENTIC;

    public static ReviewApproach orDefault(ReviewApproach value) {
        return value != null ? value : CLASSIC;
    }
}
