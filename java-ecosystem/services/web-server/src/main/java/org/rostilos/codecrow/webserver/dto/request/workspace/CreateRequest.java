package org.rostilos.codecrow.webserver.dto.request.workspace;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

public record CreateRequest(
        @NotBlank(message = "Workspace slug is required")
        @Size(min = 3, max = 64, message = "Slug must be between 3 and 64 characters")
        @Pattern(regexp = "^[a-z0-9]+(?:-[a-z0-9]+)*$", message = "Slug must contain only lowercase letters, numbers, and hyphens (no consecutive hyphens, must start/end with alphanumeric)")
        String slug,

        @NotBlank(message = "Workspace name is required")
        @Size(min = 3, max = 128, message = "Name must be between 3 and 128 characters")
        String name,

        String description
) {
}
