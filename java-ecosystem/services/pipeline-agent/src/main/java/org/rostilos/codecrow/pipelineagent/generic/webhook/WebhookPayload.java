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

    com.fasterxml.jackson.databind.JsonNode rawPayload,
    CommentData commentData
) {
    /**
     * Data about a PR comment that triggered this webhook.
     */
    public record CommentData(
        String commentId,
        String commentBody,
        String commentAuthorId,
        String commentAuthorUsername,
        String parentCommentId,
        boolean isInlineComment,
        String filePath,
        Integer lineNumber
    ) {
        /**
         * Parse a CodeCrow command from the comment body.
         * @return The parsed command, or null if no valid command is found.
         */
        public CodecrowCommand parseCommand() {
            if (commentBody == null || commentBody.isBlank()) {
                return null;
            }
            
            String body = commentBody.trim();
            if (!body.startsWith("/codecrow ")) {
                return null;
            }
            
            String commandPart = body.substring("/codecrow ".length()).trim();
            if (commandPart.isEmpty()) {
                return null;
            }
            
            // Parse command type and arguments
            String[] parts = commandPart.split("\\s+", 2);
            String commandType = parts[0].toLowerCase();
            String arguments = parts.length > 1 ? parts[1].trim() : null;
            
            return switch (commandType) {
                case "analyze" -> new CodecrowCommand(CommandType.ANALYZE, arguments);
                case "summarize" -> new CodecrowCommand(CommandType.SUMMARIZE, arguments);
                case "review" -> new CodecrowCommand(CommandType.REVIEW, arguments);
                case "ask" -> arguments != null && !arguments.isBlank() 
                    ? new CodecrowCommand(CommandType.ASK, arguments) 
                    : null;
                default -> null;
            };
        }
    }
    
    /**
     * Types of CodeCrow commands that can be triggered via comments.
     */
    public enum CommandType {
        ANALYZE,
        SUMMARIZE,
        ASK,
        REVIEW
    }
    
    /**
     * A parsed CodeCrow command from a PR comment.
     */
    public record CodecrowCommand(
        CommandType type,
        String arguments
    ) {
        public String getTypeString() {
            return type.name().toLowerCase();
        }
    }
    
    /**
     * Constructor without comment data (for backwards compatibility).
     */
    public WebhookPayload(
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
        this(provider, eventType, externalRepoId, repoSlug, workspaceSlug,
             pullRequestId, sourceBranch, targetBranch, commitHash, rawPayload, null);
    }
    
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
     * Check if this is a comment event.
     */
    public boolean isCommentEvent() {
        return commentData != null;
    }
    
    /**
     * Check if this comment contains a CodeCrow command.
     */
    public boolean hasCodecrowCommand() {
        return commentData != null && commentData.parseCommand() != null;
    }
    
    /**
     * Get the CodeCrow command from this comment, if present.
     */
    public CodecrowCommand getCodecrowCommand() {
        return commentData != null ? commentData.parseCommand() : null;
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

