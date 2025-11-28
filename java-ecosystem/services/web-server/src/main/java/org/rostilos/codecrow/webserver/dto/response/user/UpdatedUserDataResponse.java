package org.rostilos.codecrow.webserver.dto.response.user;

public class UpdatedUserDataResponse {
    private String token;
    private String type = "Bearer";
    private String username;
    private String email;
    private String company;

    public UpdatedUserDataResponse(String accessToken, String username, String email, String company) {
        this.token = accessToken;
        this.username = username;
        this.email = email;
        this.company = company;
    }

    public String getAccessToken() {
        return token;
    }

    public void setAccessToken(String accessToken) {
        this.token = accessToken;
    }

    public String getTokenType() {
        return type;
    }

    public void setTokenType(String tokenType) {
        this.type = tokenType;
    }

    public String getEmail() {
        return email;
    }

    public void setEmail(String email) {
        this.email = email;
    }

    public String getUsername() {
        return username;
    }

    public void setUsername(String username) {
        this.username = username;
    }

    public String getCompany() {
        return company;
    }

    public void setCompany(String company) {
        this.company = UpdatedUserDataResponse.this.company;
    }
}
