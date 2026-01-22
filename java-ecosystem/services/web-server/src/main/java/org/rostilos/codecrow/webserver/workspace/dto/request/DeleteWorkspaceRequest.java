package org.rostilos.codecrow.webserver.workspace.dto.request;

import jakarta.validation.constraints.NotBlank;

public record DeleteWorkspaceRequest(
    @NotBlank(message = "Confirmation slug is required")
    String confirmationSlug,
    
    @NotBlank(message = "2FA code is required")
    String twoFactorCode
) {}
