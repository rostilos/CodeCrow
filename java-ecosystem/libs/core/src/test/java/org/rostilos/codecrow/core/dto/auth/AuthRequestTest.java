package org.rostilos.codecrow.core.dto.auth;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("AuthRequest")
class AuthRequestTest {

    @Test
    @DisplayName("should create instance with default values")
    void shouldCreateInstanceWithDefaultValues() {
        AuthRequest request = new AuthRequest();
        
        assertThat(request.getUsername()).isNull();
        assertThat(request.getPassword()).isNull();
    }

    @Test
    @DisplayName("should set and get username")
    void shouldSetAndGetUsername() {
        AuthRequest request = new AuthRequest();
        request.setUsername("testuser");
        
        assertThat(request.getUsername()).isEqualTo("testuser");
    }

    @Test
    @DisplayName("should set and get password")
    void shouldSetAndGetPassword() {
        AuthRequest request = new AuthRequest();
        request.setPassword("secret123");
        
        assertThat(request.getPassword()).isEqualTo("secret123");
    }

    @Test
    @DisplayName("should allow full configuration")
    void shouldAllowFullConfiguration() {
        AuthRequest request = new AuthRequest();
        request.setUsername("admin@example.com");
        request.setPassword("adminPass!");
        
        assertThat(request.getUsername()).isEqualTo("admin@example.com");
        assertThat(request.getPassword()).isEqualTo("adminPass!");
    }
}
