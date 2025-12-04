package org.rostilos.codecrow.webserver.dto.request.auth;

import jakarta.validation.constraints.NotBlank;

public class TwoFactorVerifyRequest {
    
    @NotBlank
    private String code;

    public String getCode() {
        return code;
    }

    public void setCode(String code) {
        this.code = code;
    }
}
