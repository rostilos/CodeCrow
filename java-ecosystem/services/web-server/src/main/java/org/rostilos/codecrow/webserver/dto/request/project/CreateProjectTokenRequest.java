package org.rostilos.codecrow.webserver.dto.request.project;

public class CreateProjectTokenRequest {
    private String name;
    private String lifetime;

    public CreateProjectTokenRequest() {
    }

    public String getName() {
        return name;
    }

    public CreateProjectTokenRequest setName(String name) {
        this.name = name;
        return this;
    }

    public String getLifetime() {
        return lifetime;
    }

    public CreateProjectTokenRequest setLifetime(String lifetime) {
        this.lifetime = lifetime;
        return this;
    }
}
