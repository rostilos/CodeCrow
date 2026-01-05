package org.rostilos.codecrow.pipelineagent.webhookhandler.github;

import com.fasterxml.jackson.databind.JsonNode;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.pipelineagent.dto.webhook.WebhookPayload;
import org.rostilos.codecrow.pipelineagent.dto.webhook.WebhookPayload.CommentData;
import org.springframework.stereotype.Component;

/**
 * Parser for GitHub webhook payloads.
 * Handles PR events, push events, and issue/PR comment events.
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
        CommentData commentData = null;
        String prAuthorId = null;
        String prAuthorUsername = null;
        
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
            
            JsonNode prUser = pullRequest.path("user");
            if (!prUser.isMissingNode()) {
                prAuthorId = String.valueOf(prUser.path("id").asLong());
                prAuthorUsername = prUser.path("login").asText(null);
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
        
        // Issue comment events (includes PR comments)
        if ("issue_comment".equals(eventType) || "pull_request_review_comment".equals(eventType)) {
            commentData = parseCommentData(eventType, payload);
            
            // For issue_comment events on PRs, extract PR info from issue
            JsonNode issue = payload.path("issue");
            if (!issue.isMissingNode() && !issue.path("pull_request").isMissingNode()) {
                pullRequestId = String.valueOf(issue.path("number").asInt());
                
                // Extract PR author from issue (issue author = PR author for PR comments)
                JsonNode issueUser = issue.path("user");
                if (!issueUser.isMissingNode()) {
                    prAuthorId = String.valueOf(issueUser.path("id").asLong());
                    prAuthorUsername = issueUser.path("login").asText(null);
                }
                // Note: We may need to fetch additional PR details for commit hash
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
            payload,
            commentData,
            prAuthorId,
            prAuthorUsername
        );
    }
    
    /**
     * Parse comment data from a GitHub comment webhook payload.
     */
    private CommentData parseCommentData(String eventType, JsonNode payload) {
        JsonNode comment = payload.path("comment");
        if (comment.isMissingNode()) {
            return null;
        }
        
        String commentId = String.valueOf(comment.path("id").asLong());
        String commentBody = comment.path("body").asText(null);
        
        // Get comment author
        String authorId = null;
        String authorUsername = null;
        JsonNode user = comment.path("user");
        if (!user.isMissingNode()) {
            authorId = String.valueOf(user.path("id").asLong());
            authorUsername = user.path("login").asText(null);
        }
        
        // GitHub doesn't have parent comment concept for issue comments
        // But pull_request_review_comment has in_reply_to_id
        String parentCommentId = null;
        if (!comment.path("in_reply_to_id").isMissingNode() && !comment.path("in_reply_to_id").isNull()) {
            parentCommentId = String.valueOf(comment.path("in_reply_to_id").asLong());
        }
        
        // Check for review comment (inline comment)
        boolean isInlineComment = "pull_request_review_comment".equals(eventType);
        String filePath = null;
        Integer lineNumber = null;
        
        if (isInlineComment) {
            filePath = comment.path("path").asText(null);
            if (!comment.path("line").isMissingNode()) {
                lineNumber = comment.path("line").asInt();
            } else if (!comment.path("original_line").isMissingNode()) {
                lineNumber = comment.path("original_line").asInt();
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
