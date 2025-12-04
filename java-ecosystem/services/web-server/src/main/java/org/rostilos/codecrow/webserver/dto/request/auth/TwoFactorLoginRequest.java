package org.rostilos.codecrow.webserver.dto.request.auth;

import jakarta.validation.constraints.NotBlank;

public class TwoFactorLoginRequest {
    
    @NotBlank
    private String tempToken;
    
    @NotBlank
    private String code;

    public String getTempToken() {
        return tempToken;
    }

    public void setTempToken(String tempToken) {
        this.tempToken = tempToken;
    }

    public String getCode() {
        return code;
    }

    public void setCode(String code) {
        this.code = code;
    }
}
