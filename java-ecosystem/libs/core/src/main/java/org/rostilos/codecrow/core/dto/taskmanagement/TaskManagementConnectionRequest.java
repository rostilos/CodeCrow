package org.rostilos.codecrow.core.dto.taskmanagement;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

/**
 * Request DTO for creating or updating a task management connection.
 * <p>
 * Provider-specific credential requirements are validated at the service layer,
 * since different providers need different credential fields:
 * <ul>
 *   <li>Jira Cloud: {@code email} + {@code apiToken}</li>
 *   <li>Jira Data Center (future): {@code apiToken} only (Personal Access Token)</li>
 * </ul>
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

        /** Required for Jira Cloud; may be null for other providers. */
        String email,

        /** API token (Jira Cloud) or Personal Access Token (Jira Data Center). */
        String apiToken
) {
}
