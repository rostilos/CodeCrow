package org.rostilos.codecrow.webserver.dto.request.user;

import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.Size;

public class UpdateUserDataRequest {
    @Size(min = 3, max = 20, message = "Username must be between 3 and 20 characters")
    private String username;

    @Email(message = "Email should be valid")
    @Size(max = 50, message = "Email must not exceed 50 characters")
    private String email;


    @Size(min = 3, max = 120, message = "Company name must be between 3 and 120 characters")
    private String company;

    // Constructors
    public UpdateUserDataRequest() {}

    public UpdateUserDataRequest(
            String username,
            String email,
            String company
    ) {
        this.username = username;
        this.email = email;
        this.company = company;
    }

    // Getters and Setters
    public String getUsername() {
        return username;
    }

    public void setUsername(String username) {
        this.username = username;
    }

    public String getEmail() {
        return email;
    }

    public void setEmail(String email) {
        this.email = email;
    }

    public String getCompany() {
        return company;
    }

    public void setCompany(String company) {
        this.company = company;
    }
}