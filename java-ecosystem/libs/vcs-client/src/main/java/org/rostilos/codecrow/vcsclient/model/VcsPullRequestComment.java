package org.rostilos.codecrow.vcsclient.model;

/**
 * Provider-neutral pull-request comment used to assemble conversation context
 * for interactive CodeCrow answers.
 */
public record VcsPullRequestComment(
        String id,
        String parentId,
        String threadId,
        String authorUsername,
        String body,
        String createdAt
) {
}
