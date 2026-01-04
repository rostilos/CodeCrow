package org.rostilos.codecrow.webserver.auth.dto.request;

import jakarta.validation.constraints.NotBlank;

public class GoogleAuthRequest {
    @NotBlank
    private String credential;

    public GoogleAuthRequest() {
    }

    public GoogleAuthRequest(String credential) {
        this.credential = credential;
    }

    public String getCredential() {
        return credential;
    }

    public void setCredential(String credential) {
        this.credential = credential;
    }
}
