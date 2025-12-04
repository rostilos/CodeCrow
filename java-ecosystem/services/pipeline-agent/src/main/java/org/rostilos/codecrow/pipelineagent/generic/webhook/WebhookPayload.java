package org.rostilos.codecrow.pipelineagent.generic.webhook;

import org.rostilos.codecrow.core.model.vcs.EVcsProvider;

/**
 * Parsed webhook payload common fields.
 * Provider-specific parsers convert raw webhook payloads into this common format.
 */
public record WebhookPayload(
    EVcsProvider provider,

    String eventType,

    String externalRepoId,

    String repoSlug,

    String workspaceSlug,

    String pullRequestId,

    String sourceBranch,

    String targetBranch,

    String commitHash,

    com.fasterxml.jackson.databind.JsonNode rawPayload
) {
    /**
     * Check if this is a pull request event.
     */
    public boolean isPullRequestEvent() {
        return pullRequestId != null;
    }
    
    /**
     * Check if this is a push event.
     */
    public boolean isPushEvent() {
        return eventType != null && (
            eventType.contains("push") || 
            eventType.contains("repo:commit_status")
        );
    }
    
    /**
     * Get the full repository name (workspace/repo).
     */
    public String getFullRepoName() {
        if (workspaceSlug != null && repoSlug != null) {
            return workspaceSlug + "/" + repoSlug;
        }
        return repoSlug;
    }
}
