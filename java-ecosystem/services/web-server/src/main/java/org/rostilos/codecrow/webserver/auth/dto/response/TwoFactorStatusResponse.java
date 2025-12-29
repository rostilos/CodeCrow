package org.rostilos.codecrow.webserver.auth.dto.response;

public class TwoFactorStatusResponse {
    
    private boolean enabled;
    private String type;
    private int remainingBackupCodes;

    public TwoFactorStatusResponse() {
    }

    public TwoFactorStatusResponse(boolean enabled, String type, int remainingBackupCodes) {
        this.enabled = enabled;
        this.type = type;
        this.remainingBackupCodes = remainingBackupCodes;
    }

    public boolean isEnabled() {
        return enabled;
    }

    public void setEnabled(boolean enabled) {
        this.enabled = enabled;
    }

    public String getType() {
        return type;
    }

    public void setType(String type) {
        this.type = type;
    }

    public int getRemainingBackupCodes() {
        return remainingBackupCodes;
    }

    public void setRemainingBackupCodes(int remainingBackupCodes) {
        this.remainingBackupCodes = remainingBackupCodes;
    }
}
