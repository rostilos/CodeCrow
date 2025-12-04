package org.rostilos.codecrow.webserver.dto.response.auth;

public class TwoFactorRequiredResponse {
    
    private boolean requiresTwoFactor;
    private String tempToken;
    private String twoFactorType;
    private String message;

    public TwoFactorRequiredResponse() {
    }

    public TwoFactorRequiredResponse(boolean requiresTwoFactor, String tempToken, String twoFactorType, String message) {
        this.requiresTwoFactor = requiresTwoFactor;
        this.tempToken = tempToken;
        this.twoFactorType = twoFactorType;
        this.message = message;
    }

    public boolean isRequiresTwoFactor() {
        return requiresTwoFactor;
    }

    public void setRequiresTwoFactor(boolean requiresTwoFactor) {
        this.requiresTwoFactor = requiresTwoFactor;
    }

    public String getTempToken() {
        return tempToken;
    }

    public void setTempToken(String tempToken) {
        this.tempToken = tempToken;
    }

    public String getTwoFactorType() {
        return twoFactorType;
    }

    public void setTwoFactorType(String twoFactorType) {
        this.twoFactorType = twoFactorType;
    }

    public String getMessage() {
        return message;
    }

    public void setMessage(String message) {
        this.message = message;
    }
}
