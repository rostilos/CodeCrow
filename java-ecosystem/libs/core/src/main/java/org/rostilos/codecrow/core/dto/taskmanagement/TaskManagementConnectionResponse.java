package org.rostilos.codecrow.core.dto.taskmanagement;

import java.time.OffsetDateTime;

/**
 * Response DTO for task management connection details.
 * Excludes sensitive credential data.
 */
public record TaskManagementConnectionResponse(
        Long id,
        String connectionName,
        String providerType,
        String status,
        String baseUrl,
        /** Masked email (e.g. "j***@example.com") — never returns raw credentials */
        String maskedEmail,
        OffsetDateTime createdAt,
        OffsetDateTime updatedAt
) {
}
