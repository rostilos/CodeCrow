package org.rostilos.codecrow.vcsclient.model;

/**
 * Provider-neutral pull/merge request metadata used by analysis consumers.
 */
public record VcsPullRequest(
        long number,
        String title,
        String description,
        String sourceBranch,
        String targetBranch,
        String baseCommit,
        String headCommit,
        String state,
        boolean merged,
        String webUrl
) {
}
