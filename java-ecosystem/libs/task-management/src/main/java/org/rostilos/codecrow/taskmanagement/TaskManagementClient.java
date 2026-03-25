package org.rostilos.codecrow.taskmanagement;

import org.rostilos.codecrow.taskmanagement.model.TaskComment;
import org.rostilos.codecrow.taskmanagement.model.TaskDetails;

import java.io.IOException;
import java.util.List;
import java.util.Optional;

/**
 * Provider-agnostic interface for task management platform integrations.
 * <p>
 * Implementations exist for Jira Cloud (v1) with Jira Data Center planned.
 * Future providers (Linear, Azure DevOps, GitHub Issues, etc.) will implement
 * this same interface.
 * </p>
 *
 * <p>All methods throw {@link IOException} for transport-level failures.
 * Provider-specific errors are wrapped in {@link TaskManagementException}.</p>
 */
public interface TaskManagementClient {

    /**
     * Validate that the configured credentials can authenticate
     * and have sufficient permissions.
     *
     * @return {@code true} if the connection is valid
     * @throws IOException on transport failure
     * @throws TaskManagementException on auth/permission failure
     */
    boolean validateConnection() throws IOException;

    /**
     * Fetch task details (summary, description, status, assignee, etc.).
     *
     * @param taskId the task identifier (e.g. "WS-111", "GR-2499")
     * @return task details
     * @throws IOException on transport failure
     * @throws TaskManagementException if the task does not exist or is inaccessible
     */
    TaskDetails getTaskDetails(String taskId) throws IOException;

    /**
     * List all comments on a task.
     *
     * @param taskId the task identifier
     * @return list of comments, ordered chronologically
     * @throws IOException on transport failure
     */
    List<TaskComment> getComments(String taskId) throws IOException;

    /**
     * Post a new comment to a task.
     *
     * @param taskId  the task identifier
     * @param body    comment body (plain text or provider-specific markup)
     * @return the created comment
     * @throws IOException on transport failure
     */
    TaskComment postComment(String taskId, String body) throws IOException;

    /**
     * Update an existing comment.
     *
     * @param taskId    the task identifier
     * @param commentId the provider-specific comment ID
     * @param body      new comment body
     * @return the updated comment
     * @throws IOException on transport failure
     */
    TaskComment updateComment(String taskId, String commentId, String body) throws IOException;

    /**
     * Delete a comment.
     *
     * @param taskId    the task identifier
     * @param commentId the provider-specific comment ID
     * @throws IOException on transport failure
     */
    void deleteComment(String taskId, String commentId) throws IOException;

    /**
     * Search for a comment containing a specific marker string.
     * Used to detect existing auto-documentation comments via an embedded
     * hidden marker (e.g. {@code <!-- codecrow-qa-autodoc -->}).
     *
     * @param taskId the task identifier
     * @param marker the marker string to search for in comment bodies
     * @return the matching comment, or empty if not found
     * @throws IOException on transport failure
     */
    Optional<TaskComment> findCommentByMarker(String taskId, String marker) throws IOException;

    /**
     * Returns the platform this client connects to.
     */
    ETaskManagementPlatform getPlatform();
}
