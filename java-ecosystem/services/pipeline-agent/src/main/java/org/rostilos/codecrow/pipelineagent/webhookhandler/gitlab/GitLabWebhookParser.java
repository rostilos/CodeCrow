package org.rostilos.codecrow.pipelineagent.webhookhandler.gitlab;

import com.fasterxml.jackson.databind.JsonNode;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.pipelineagent.dto.webhook.WebhookPayload;
import org.rostilos.codecrow.pipelineagent.dto.webhook.WebhookPayload.CommentData;
import org.springframework.stereotype.Component;

/**
 * Parser for GitLab webhook payloads.
 * Handles MR events, push events, and note (comment) events.
 */
@Component
public class GitLabWebhookParser {
    
    /**
     * Parse a GitLab webhook payload.
     *
     * @param eventType The X-Gitlab-Event header value
     * @param payload The raw JSON payload
     * @return Parsed webhook payload
     */
    public WebhookPayload parse(String eventType, JsonNode payload) {
        String externalRepoId = null;
        String repoSlug = null;
        String namespace = null;
        String mergeRequestIid = null;
        String sourceBranch = null;
        String targetBranch = null;
        String commitHash = null;
        CommentData commentData = null;
        String mrAuthorId = null;
        String mrAuthorUsername = null;
        
        // GitLab uses "project" for repository info
        JsonNode project = payload.path("project");
        if (!project.isMissingNode()) {
            externalRepoId = String.valueOf(project.path("id").asLong());
            repoSlug = project.path("path").asText(null);
            
            // Extract namespace from path_with_namespace
            String pathWithNamespace = project.path("path_with_namespace").asText(null);
            if (pathWithNamespace != null && pathWithNamespace.contains("/")) {
                namespace = pathWithNamespace.substring(0, pathWithNamespace.lastIndexOf('/'));
            }
        }
        
        // Map GitLab event types to normalized event types
        String normalizedEventType = normalizeEventType(eventType);
        
        // Merge Request events
        JsonNode objectAttributes = payload.path("object_attributes");
        if (!objectAttributes.isMissingNode()) {
            if ("Merge Request Hook".equals(eventType) || "merge_request".equals(eventType)) {
                mergeRequestIid = String.valueOf(objectAttributes.path("iid").asInt());
                sourceBranch = objectAttributes.path("source_branch").asText(null);
                targetBranch = objectAttributes.path("target_branch").asText(null);
                commitHash = objectAttributes.path("last_commit").path("id").asText(null);
                
                // Extract MR author
                JsonNode author = objectAttributes.path("last_commit").path("author");
                if (!author.isMissingNode()) {
                    mrAuthorUsername = author.path("name").asText(null);
                }
                // Try user attribute
                JsonNode user = payload.path("user");
                if (!user.isMissingNode()) {
                    mrAuthorId = String.valueOf(user.path("id").asLong());
                    if (mrAuthorUsername == null) {
                        mrAuthorUsername = user.path("username").asText(null);
                    }
                }
            }
            
            // Note (comment) events
            if ("Note Hook".equals(eventType) || "note".equals(eventType)) {
                commentData = parseCommentData(payload);
                
                // For notes on MRs, get MR info
                JsonNode mergeRequest = payload.path("merge_request");
                if (!mergeRequest.isMissingNode()) {
                    mergeRequestIid = String.valueOf(mergeRequest.path("iid").asInt());
                    sourceBranch = mergeRequest.path("source_branch").asText(null);
                    targetBranch = mergeRequest.path("target_branch").asText(null);
                    commitHash = mergeRequest.path("last_commit").path("id").asText(null);
                    
                    mrAuthorId = String.valueOf(mergeRequest.path("author_id").asLong());
                }
            }
        }
        
        // Push events
        if ("Push Hook".equals(eventType) || "push".equals(eventType)) {
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
            EVcsProvider.GITLAB,
            normalizedEventType,
            externalRepoId,
            repoSlug,
            namespace,
            mergeRequestIid,
            sourceBranch,
            targetBranch,
            commitHash,
            payload,
            commentData,
            mrAuthorId,
            mrAuthorUsername
        );
    }
    
    /**
     * Normalize GitLab event types to common event names.
     */
    private String normalizeEventType(String gitlabEventType) {
        if (gitlabEventType == null) return null;
        
        return switch (gitlabEventType) {
            case "Merge Request Hook" -> "merge_request";
            case "Push Hook" -> "push";
            case "Note Hook" -> "note";
            case "Tag Push Hook" -> "tag_push";
            case "Issue Hook" -> "issue";
            case "Pipeline Hook" -> "pipeline";
            case "Job Hook" -> "job";
            default -> gitlabEventType.toLowerCase().replace(" hook", "");
        };
    }
    
    /**
     * Parse comment data from a GitLab note webhook payload.
     */
    private CommentData parseCommentData(JsonNode payload) {
        JsonNode objectAttributes = payload.path("object_attributes");
        if (objectAttributes.isMissingNode()) {
            return null;
        }
        
        String noteableType = objectAttributes.path("noteable_type").asText(null);
        if (!"MergeRequest".equals(noteableType)) {
            // Only handle MR comments
            return null;
        }
        
        String commentId = String.valueOf(objectAttributes.path("id").asLong());
        String commentBody = objectAttributes.path("note").asText(null);
        
        // Get comment author
        String authorId = null;
        String authorUsername = null;
        JsonNode user = payload.path("user");
        if (!user.isMissingNode()) {
            authorId = String.valueOf(user.path("id").asLong());
            authorUsername = user.path("username").asText(null);
        }
        
        // GitLab uses discussion_id for threaded comments
        String parentCommentId = null;
        String discussionId = objectAttributes.path("discussion_id").asText(null);
        // If this is a reply, the discussion_id references the parent
        if (discussionId != null && objectAttributes.path("type").asText("").equals("DiscussionNote")) {
            parentCommentId = discussionId;
        }
        
        // Check if this is an inline comment (on a specific file/line)
        boolean isInlineComment = !objectAttributes.path("position").isMissingNode();
        String filePath = null;
        Integer lineNumber = null;
        
        if (isInlineComment) {
            JsonNode position = objectAttributes.path("position");
            filePath = position.path("new_path").asText(null);
            if (filePath == null) {
                filePath = position.path("old_path").asText(null);
            }
            
            if (!position.path("new_line").isMissingNode()) {
                lineNumber = position.path("new_line").asInt();
            } else if (!position.path("old_line").isMissingNode()) {
                lineNumber = position.path("old_line").asInt();
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
}
