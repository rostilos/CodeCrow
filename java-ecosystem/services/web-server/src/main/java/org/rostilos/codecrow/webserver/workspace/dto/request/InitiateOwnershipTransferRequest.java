package org.rostilos.codecrow.webserver.workspace.dto.request;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;

public record InitiateOwnershipTransferRequest(
        @NotNull(message = "Target user ID cannot be null")
        Long targetUserId,
        @NotBlank(message = "Two-factor code is required")
        String twoFactorCode
) {
}
