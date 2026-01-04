package org.rostilos.codecrow.webserver.auth.dto.response;

public class ResetTokenValidationResponse {
    
    private boolean valid;
    private boolean twoFactorRequired;
    private String twoFactorType;
    private String email;
    private String message;
    
    public ResetTokenValidationResponse() {
    }
    
    public ResetTokenValidationResponse(boolean valid, boolean twoFactorRequired, String twoFactorType, String email, String message) {
        this.valid = valid;
        this.twoFactorRequired = twoFactorRequired;
        this.twoFactorType = twoFactorType;
        this.email = email;
        this.message = message;
    }
    
    public static ResetTokenValidationResponse invalid(String message) {
        return new ResetTokenValidationResponse(false, false, null, null, message);
    }
    
    public static ResetTokenValidationResponse valid(String email, boolean twoFactorRequired, String twoFactorType) {
        return new ResetTokenValidationResponse(true, twoFactorRequired, twoFactorType, email, "Token is valid");
    }
    
    public boolean isValid() {
        return valid;
    }
    
    public void setValid(boolean valid) {
        this.valid = valid;
    }
    
    public boolean isTwoFactorRequired() {
        return twoFactorRequired;
    }
    
    public void setTwoFactorRequired(boolean twoFactorRequired) {
        this.twoFactorRequired = twoFactorRequired;
    }
    
    public String getTwoFactorType() {
        return twoFactorType;
    }
    
    public void setTwoFactorType(String twoFactorType) {
        this.twoFactorType = twoFactorType;
    }
    
    public String getEmail() {
        return email;
    }
    
    public void setEmail(String email) {
        this.email = email;
    }
    
    public String getMessage() {
        return message;
    }
    
    public void setMessage(String message) {
        this.message = message;
    }
}
