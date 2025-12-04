package org.rostilos.codecrow.webserver.dto.response.auth;

public class TwoFactorEnableResponse {
    
    private String[] backupCodes;
    private boolean success;
    private String message;

    public TwoFactorEnableResponse() {
    }

    public TwoFactorEnableResponse(String[] backupCodes, boolean success, String message) {
        this.backupCodes = backupCodes;
        this.success = success;
        this.message = message;
    }

    public String[] getBackupCodes() {
        return backupCodes;
    }

    public void setBackupCodes(String[] backupCodes) {
        this.backupCodes = backupCodes;
    }

    public boolean isSuccess() {
        return success;
    }

    public void setSuccess(boolean success) {
        this.success = success;
    }

    public String getMessage() {
        return message;
    }

    public void setMessage(String message) {
        this.message = message;
    }
}
