package org.rostilos.codecrow.webserver.dto.request.auth;

import jakarta.validation.constraints.NotBlank;

public class ValidateResetTokenRequest {
    
    @NotBlank(message = "Token is required")
    private String token;
    
    public ValidateResetTokenRequest() {
    }
    
    public ValidateResetTokenRequest(String token) {
        this.token = token;
    }
    
    public String getToken() {
        return token;
    }
    
    public void setToken(String token) {
        this.token = token;
    }
}
