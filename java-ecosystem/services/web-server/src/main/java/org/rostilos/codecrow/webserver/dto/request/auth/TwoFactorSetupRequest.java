package org.rostilos.codecrow.webserver.dto.request.auth;

import jakarta.validation.constraints.NotBlank;

public class TwoFactorSetupRequest {
    
    @NotBlank
    private String type; // "TOTP" or "EMAIL"

    public String getType() {
        return type;
    }

    public void setType(String type) {
        this.type = type;
    }
}
