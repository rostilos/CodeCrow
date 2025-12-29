package org.rostilos.codecrow.webserver.workspace.dto.request;

import jakarta.validation.constraints.NotBlank;

public record RemoveMemberRequest(
        @NotBlank(message = "Username cannot be empty")
        String username
) {
}
