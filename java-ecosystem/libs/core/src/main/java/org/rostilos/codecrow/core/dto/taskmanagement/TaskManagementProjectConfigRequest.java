package org.rostilos.codecrow.core.dto.taskmanagement;

import jakarta.validation.constraints.Size;

/**
 * Request DTO for binding a project to a task-management connection and task
 * key extraction strategy.
 */
public record TaskManagementProjectConfigRequest(
        Long taskManagementConnectionId,

        @Size(max = 256, message = "Task ID pattern must be at most 256 characters")
        String taskIdPattern,

        /** Where to look for the task ID: BRANCH_NAME, PR_TITLE, PR_DESCRIPTION. */
        String taskIdSource
) {
}
