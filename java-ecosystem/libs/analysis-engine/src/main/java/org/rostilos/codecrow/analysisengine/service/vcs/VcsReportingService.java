package org.rostilos.codecrow.analysisengine.service.vcs;

import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;

import java.io.IOException;

/**
 * Interface for VCS-specific analysis result reporting.
 * Each VCS provider (Bitbucket, GitHub, GitLab) implements this to handle
 * provider-specific operations like posting comments and reports.
 */
public interface VcsReportingService {

    EVcsProvider getProvider();

    /**
     * Posts complete analysis results to the VCS platform.
     *
     * @param codeAnalysis      The code analysis results
     * @param project           The project entity
     * @param pullRequestNumber The PR number on the VCS platform
     * @param platformPrEntityId The internal PR entity ID
     */
    void postAnalysisResults(
            CodeAnalysis codeAnalysis,
            Project project,
            Long pullRequestNumber,
            Long platformPrEntityId
    ) throws IOException;
    
    /**
     * Post a comment to a pull request.
     * 
     * @param project The project entity
     * @param pullRequestNumber The PR number
     * @param content The comment content (markdown)
     * @param marker Optional marker for identifying the comment later (for deletion)
     * @return The ID of the created comment
     */
    default String postComment(
            Project project,
            Long pullRequestNumber,
            String content,
            String marker
    ) throws IOException {
        throw new UnsupportedOperationException("postComment not implemented for this provider");
    }
    
    /**
     * Post a reply to an existing comment.
     * 
     * @param project The project entity
     * @param pullRequestNumber The PR number
     * @param parentCommentId The ID of the comment to reply to
     * @param content The reply content (markdown)
     * @return The ID of the created reply
     */
    default String postCommentReply(
            Project project,
            Long pullRequestNumber,
            String parentCommentId,
            String content
    ) throws IOException {
        throw new UnsupportedOperationException("postCommentReply not implemented for this provider");
    }
    
    /**
     * Delete comments matching a specific marker.
     * 
     * @param project The project entity
     * @param pullRequestNumber The PR number
     * @param marker The marker to identify comments to delete
     * @return Number of comments deleted
     */
    default int deleteCommentsByMarker(
            Project project,
            Long pullRequestNumber,
            String marker
    ) throws IOException {
        throw new UnsupportedOperationException("deleteCommentsByMarker not implemented for this provider");
    }
    
    /**
     * Delete a specific comment by ID.
     * 
     * @param project The project entity
     * @param pullRequestNumber The PR number
     * @param commentId The ID of the comment to delete
     */
    default void deleteComment(
            Project project,
            Long pullRequestNumber,
            String commentId
    ) throws IOException {
        throw new UnsupportedOperationException("deleteComment not implemented for this provider");
    }
    
    /**
     * Post a reply to an existing comment with additional context.
     * For platforms that don't support threading (GitHub), this will format
     * the reply with a quote and mention.
     * 
     * @param project The project entity
     * @param pullRequestNumber The PR number
     * @param parentCommentId The ID of the comment to reply to
     * @param content The reply content (markdown)
     * @param originalAuthorUsername Username of original comment author (for @mention)
     * @param originalCommentBody Original comment body (for quoting)
     * @return The ID of the created reply
     */
    default String postCommentReplyWithContext(
            Project project,
            Long pullRequestNumber,
            String parentCommentId,
            String content,
            String originalAuthorUsername,
            String originalCommentBody
    ) throws IOException {
        // Default: fall back to basic reply
        return postCommentReply(project, pullRequestNumber, parentCommentId, content);
    }
    
    /**
     * Check if the provider supports Mermaid diagrams in comments.
     * @return true if Mermaid is supported, false for ASCII fallback
     */
    default boolean supportsMermaidDiagrams() {
        return false;
    }
    
    /**
     * Delete previous comments of a specific type (summarize, review).
     * This is used to ensure only one comment of each type exists.
     * 
     * @param project The project entity
     * @param pullRequestNumber The PR number
     * @param commentType The type of comment to delete ("summarize" or "review")
     * @return Number of comments deleted
     */
    default int deletePreviousCommentsByType(
            Project project,
            Long pullRequestNumber,
            String commentType
    ) throws IOException {
        return 0; // Default implementation does nothing
    }
}
