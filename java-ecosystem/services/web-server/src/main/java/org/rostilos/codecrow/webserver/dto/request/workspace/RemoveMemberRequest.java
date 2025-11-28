package org.rostilos.codecrow.webserver.dto.request.workspace;

import jakarta.validation.constraints.NotBlank;

public record RemoveMemberRequest(
        @NotBlank(message = "Username cannot be empty")
        String username
) {
}
