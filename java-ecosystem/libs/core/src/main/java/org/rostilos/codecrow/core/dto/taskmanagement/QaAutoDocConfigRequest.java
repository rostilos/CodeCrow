package org.rostilos.codecrow.core.dto.taskmanagement;

import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

/**
 * Request DTO for updating QA auto-documentation configuration on a project.
 */
public record QaAutoDocConfigRequest(
        @NotNull(message = "Enabled flag is required")
        Boolean enabled,

        Long taskManagementConnectionId,

        /** Regex pattern for extracting task ID from PR metadata */
        @Size(max = 256, message = "Task ID pattern must be at most 256 characters")
        String taskIdPattern,

        /** Where to look for the task ID: BRANCH_NAME, PR_TITLE, PR_DESCRIPTION */
        String taskIdSource,

        /** Template mode: RAW, BASE, CUSTOM */
        String templateMode,

        /** Custom template text (only for CUSTOM mode, max 5000 chars) */
        @Size(max = 5000, message = "Custom template must be at most 5000 characters")
        String customTemplate
) {
}
