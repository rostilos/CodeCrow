package org.rostilos.codecrow.pipelineagent.bitbucket.webhook;

import com.fasterxml.jackson.databind.JsonNode;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.pipelineagent.generic.webhook.WebhookPayload;
import org.springframework.stereotype.Component;

/**
 * Parser for Bitbucket Cloud webhook payloads.
 */
@Component
public class BitbucketCloudWebhookParser {
    
    /**
     * Parse a Bitbucket Cloud webhook payload.
     *
     * @param eventType The X-Event-Key header value
     * @param payload The raw JSON payload
     * @return Parsed webhook payload
     */
    public WebhookPayload parse(String eventType, JsonNode payload) {
        String externalRepoId = null;
        String repoSlug = null;
        String workspaceSlug = null;
        String pullRequestId = null;
        String sourceBranch = null;
        String targetBranch = null;
        String commitHash = null;
        
        JsonNode repository = payload.path("repository");
        if (!repository.isMissingNode()) {
            externalRepoId = extractUuid(repository.path("uuid"));
            repoSlug = repository.path("name").asText(null);
            
            JsonNode workspace = repository.path("workspace");
            if (!workspace.isMissingNode()) {
                workspaceSlug = workspace.path("slug").asText(null);
            } else {
                JsonNode owner = repository.path("owner");
                if (!owner.isMissingNode()) {
                    workspaceSlug = owner.path("username").asText(null);
                }
            }
        }
        
        JsonNode pullRequest = payload.path("pullrequest");
        if (!pullRequest.isMissingNode()) {
            pullRequestId = String.valueOf(pullRequest.path("id").asInt());
            
            JsonNode source = pullRequest.path("source");
            if (!source.isMissingNode()) {
                sourceBranch = source.path("branch").path("name").asText(null);
                commitHash = source.path("commit").path("hash").asText(null);
            }
            
            JsonNode destination = pullRequest.path("destination");
            if (!destination.isMissingNode()) {
                targetBranch = destination.path("branch").path("name").asText(null);
            }
        }
        
        JsonNode push = payload.path("push");
        if (!push.isMissingNode()) {
            JsonNode changes = push.path("changes");
            if (changes.isArray() && changes.size() > 0) {
                JsonNode firstChange = changes.get(0);
                JsonNode newNode = firstChange.path("new");
                if (!newNode.isMissingNode()) {
                    sourceBranch = newNode.path("name").asText(null);
                    JsonNode target = newNode.path("target");
                    if (!target.isMissingNode()) {
                        commitHash = target.path("hash").asText(null);
                    }
                }
            }
        }
        
        return new WebhookPayload(
            EVcsProvider.BITBUCKET_CLOUD,
            eventType,
            externalRepoId,
            repoSlug,
            workspaceSlug,
            pullRequestId,
            sourceBranch,
            targetBranch,
            commitHash,
            payload
        );
    }
    
    private String extractUuid(JsonNode uuidNode) {
        if (uuidNode.isMissingNode() || uuidNode.isNull()) {
            return null;
        }
        String uuid = uuidNode.asText();
        return uuid.replace("{", "").replace("}", "");
    }
}
