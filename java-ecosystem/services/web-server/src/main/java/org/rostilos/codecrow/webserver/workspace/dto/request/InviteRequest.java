package org.rostilos.codecrow.webserver.workspace.dto.request;

import jakarta.validation.constraints.NotBlank;
import org.rostilos.codecrow.core.utils.EnumNamePattern;

public record InviteRequest(
        @NotBlank(message = "Username cannot be empty")
        String username,
        @EnumNamePattern(
                regexp = "ADMIN|MEMBER|REVIEWER",
                message = "Role must be one of the following: ADMIN, MEMBER, REVIEWER"
        )
        String role
) {
}
