package org.rostilos.codecrow.testsupport.fixture;

import java.time.LocalDateTime;

/**
 * Builder for creating test User entities.
 * Uses the entity's constructor / setters to build valid test objects.
 */
public final class UserFixture {

    private String username = "testuser";
    private String email = "test@codecrow.dev";
    private String password = "encrypted_password_hash";
    private String provider = "local";
    private String providerId = null;
    private String avatarUrl = null;

    private UserFixture() {
    }

    public static UserFixture aUser() {
        return new UserFixture();
    }

    public UserFixture withUsername(String username) {
        this.username = username;
        return this;
    }

    public UserFixture withEmail(String email) {
        this.email = email;
        return this;
    }

    public UserFixture withPassword(String password) {
        this.password = password;
        return this;
    }

    public UserFixture withProvider(String provider, String providerId) {
        this.provider = provider;
        this.providerId = providerId;
        return this;
    }

    public UserFixture withAvatarUrl(String avatarUrl) {
        this.avatarUrl = avatarUrl;
        return this;
    }

    /**
     * Build a User entity via reflection-free approach:
     * returns a map of field values that test code can use to create or insert.
     * For direct entity construction, callers should use new User(username, email, password).
     */
    public java.util.Map<String, Object> asMap() {
        var map = new java.util.LinkedHashMap<String, Object>();
        map.put("username", username);
        map.put("email", email);
        map.put("password", password);
        map.put("provider", provider);
        map.put("providerId", providerId);
        map.put("avatarUrl", avatarUrl);
        return map;
    }

    public String getUsername() { return username; }
    public String getEmail() { return email; }
    public String getPassword() { return password; }
    public String getProvider() { return provider; }
    public String getProviderId() { return providerId; }
    public String getAvatarUrl() { return avatarUrl; }
}
