package org.rostilos.codecrow.webserver.auth.dto.response;

public class TwoFactorSetupResponse {
    
    private String secretKey;
    private String qrCodeUrl;
    private String type;
    private boolean verified;

    public TwoFactorSetupResponse() {
    }

    public TwoFactorSetupResponse(String secretKey, String qrCodeUrl, String type, boolean verified) {
        this.secretKey = secretKey;
        this.qrCodeUrl = qrCodeUrl;
        this.type = type;
        this.verified = verified;
    }

    public String getSecretKey() {
        return secretKey;
    }

    public void setSecretKey(String secretKey) {
        this.secretKey = secretKey;
    }

    public String getQrCodeUrl() {
        return qrCodeUrl;
    }

    public void setQrCodeUrl(String qrCodeUrl) {
        this.qrCodeUrl = qrCodeUrl;
    }

    public String getType() {
        return type;
    }

    public void setType(String type) {
        this.type = type;
    }

    public boolean isVerified() {
        return verified;
    }

    public void setVerified(boolean verified) {
        this.verified = verified;
    }
}
