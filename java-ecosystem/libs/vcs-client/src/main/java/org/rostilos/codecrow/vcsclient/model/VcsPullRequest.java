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
        String targetHeadCommit,
        String baseCommit,
        String headCommit,
        String state,
        boolean merged,
        String webUrl
) {
    /**
     * Source-compatible constructor for callers that predate the distinct
     * target-head field. Historically {@code baseCommit} was also used as the
     * target revision, so retain that fallback for those callers.
     */
    public VcsPullRequest(
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
        this(
                number,
                title,
                description,
                sourceBranch,
                targetBranch,
                baseCommit,
                baseCommit,
                headCommit,
                state,
                merged,
                webUrl);
    }
}
