package org.rostilos.codecrow.pipelineagent.bitbucket.webhook;

import com.fasterxml.jackson.databind.JsonNode;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.pipelineagent.generic.webhook.WebhookPayload;
import org.rostilos.codecrow.pipelineagent.generic.webhook.WebhookPayload.CommentData;
import org.springframework.stereotype.Component;

/**
 * Parser for Bitbucket Cloud webhook payloads.
 * Handles PR events, push events, and PR comment events.
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
        CommentData commentData = null;
        
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
        
        if (eventType != null && eventType.contains("comment")) {
            commentData = parseCommentData(payload);
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
            payload,
            commentData
        );
    }
    
    /**
     * Parse comment data from a Bitbucket Cloud PR comment webhook payload.
     */
    private CommentData parseCommentData(JsonNode payload) {
        JsonNode comment = payload.path("comment");
        if (comment.isMissingNode()) {
            return null;
        }
        
        String commentId = String.valueOf(comment.path("id").asLong());
        String commentBody = comment.path("content").path("raw").asText(null);
        
        // Get comment author
        String authorId = null;
        String authorUsername = null;
        JsonNode user = comment.path("user");
        if (!user.isMissingNode()) {
            authorId = extractUuid(user.path("uuid"));
            authorUsername = user.path("username").asText(
                user.path("nickname").asText(null)
            );
        }
        
        // Check for parent comment (reply)
        String parentCommentId = null;
        JsonNode parent = comment.path("parent");
        if (!parent.isMissingNode() && !parent.isNull()) {
            parentCommentId = String.valueOf(parent.path("id").asLong());
        }
        
        // Check for inline comment data
        boolean isInlineComment = false;
        String filePath = null;
        Integer lineNumber = null;
        
        JsonNode inline = comment.path("inline");
        if (!inline.isMissingNode() && !inline.isNull()) {
            isInlineComment = true;
            filePath = inline.path("path").asText(null);
            if (!inline.path("to").isMissingNode()) {
                lineNumber = inline.path("to").asInt();
            } else if (!inline.path("from").isMissingNode()) {
                lineNumber = inline.path("from").asInt();
            }
        }
        
        return new CommentData(
            commentId,
            commentBody,
            authorId,
            authorUsername,
            parentCommentId,
            isInlineComment,
            filePath,
            lineNumber
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
