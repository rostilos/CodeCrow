package org.rostilos.codecrow.core.dto.taskmanagement;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

/**
 * Request DTO for creating or updating a task management connection.
 */
public record TaskManagementConnectionRequest(
        @NotBlank(message = "Connection name is required")
        @Size(max = 256, message = "Connection name must be at most 256 characters")
        String connectionName,

        @NotNull(message = "Provider type is required")
        String providerType,

        @NotBlank(message = "Base URL is required")
        @Size(max = 512, message = "Base URL must be at most 512 characters")
        String baseUrl,

        @NotBlank(message = "Email is required for Jira Cloud authentication")
        String email,

        @NotBlank(message = "API token is required")
        String apiToken
) {
}
