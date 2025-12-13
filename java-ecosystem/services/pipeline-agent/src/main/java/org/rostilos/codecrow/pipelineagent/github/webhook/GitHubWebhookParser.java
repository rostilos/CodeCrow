package org.rostilos.codecrow.pipelineagent.github.webhook;

import com.fasterxml.jackson.databind.JsonNode;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.pipelineagent.generic.webhook.WebhookPayload;
import org.springframework.stereotype.Component;

/**
 * Parser for GitHub webhook payloads.
 */
@Component
public class GitHubWebhookParser {
    
    /**
     * Parse a GitHub webhook payload.
     *
     * @param eventType The X-GitHub-Event header value
     * @param payload The raw JSON payload
     * @return Parsed webhook payload
     */
    public WebhookPayload parse(String eventType, JsonNode payload) {
        String externalRepoId = null;
        String repoSlug = null;
        String owner = null;
        String pullRequestId = null;
        String sourceBranch = null;
        String targetBranch = null;
        String commitHash = null;
        
        JsonNode repository = payload.path("repository");
        if (!repository.isMissingNode()) {
            externalRepoId = String.valueOf(repository.path("id").asLong());
            repoSlug = repository.path("name").asText(null);
            
            JsonNode ownerNode = repository.path("owner");
            if (!ownerNode.isMissingNode()) {
                owner = ownerNode.path("login").asText(null);
            }
        }
        
        // Pull request events
        JsonNode pullRequest = payload.path("pull_request");
        if (!pullRequest.isMissingNode()) {
            pullRequestId = String.valueOf(pullRequest.path("number").asInt());
            
            JsonNode head = pullRequest.path("head");
            if (!head.isMissingNode()) {
                sourceBranch = head.path("ref").asText(null);
                commitHash = head.path("sha").asText(null);
            }
            
            JsonNode base = pullRequest.path("base");
            if (!base.isMissingNode()) {
                targetBranch = base.path("ref").asText(null);
            }
        }
        
        // Push events
        if ("push".equals(eventType)) {
            String ref = payload.path("ref").asText(null);
            if (ref != null && ref.startsWith("refs/heads/")) {
                sourceBranch = ref.substring("refs/heads/".length());
            }
            
            commitHash = payload.path("after").asText(null);
            
            // Skip if this is a branch deletion (all zeros commit)
            if ("0000000000000000000000000000000000000000".equals(commitHash)) {
                commitHash = null;
            }
        }
        
        return new WebhookPayload(
            EVcsProvider.GITHUB,
            eventType,
            externalRepoId,
            repoSlug,
            owner,
            pullRequestId,
            sourceBranch,
            targetBranch,
            commitHash,
            payload
        );
    }
}
