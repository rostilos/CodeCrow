package org.rostilos.codecrow.webserver.auth.dto.request;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

public class ResetPasswordRequest {
    
    @NotBlank(message = "Reset token is required")
    private String token;
    
    @NotBlank(message = "New password is required")
    @Size(min = 8, max = 120, message = "Password must be between 8 and 120 characters")
    private String newPassword;
    
    // Optional - only required if user has 2FA enabled
    private String twoFactorCode;
    
    public ResetPasswordRequest() {
    }
    
    public ResetPasswordRequest(String token, String newPassword, String twoFactorCode) {
        this.token = token;
        this.newPassword = newPassword;
        this.twoFactorCode = twoFactorCode;
    }
    
    public String getToken() {
        return token;
    }
    
    public void setToken(String token) {
        this.token = token;
    }
    
    public String getNewPassword() {
        return newPassword;
    }
    
    public void setNewPassword(String newPassword) {
        this.newPassword = newPassword;
    }
    
    public String getTwoFactorCode() {
        return twoFactorCode;
    }
    
    public void setTwoFactorCode(String twoFactorCode) {
        this.twoFactorCode = twoFactorCode;
    }
}
