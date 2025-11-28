package org.rostilos.codecrow.webserver.dto.request.workspace;

import jakarta.validation.constraints.NotBlank;
import org.rostilos.codecrow.core.utils.EnumNamePattern;

public record ChangeRoleRequest(
        @NotBlank(message = "Username cannot be empty")
        String username,
        @EnumNamePattern(
                regexp = "OWNER|ADMIN|MEMBER|VIEWER", // List your valid EWorkspaceRole names here
                message = "Role must be one of the following: OWNER, ADMIN, MEMBER, VIEWER"
        )
        String newRole
) {
}
