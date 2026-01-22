package org.rostilos.codecrow.core.dto.auth;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("AuthResponse")
class AuthResponseTest {

    @Test
    @DisplayName("should create instance with jwt")
    void shouldCreateInstanceWithJwt() {
        AuthResponse response = new AuthResponse("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9");
        
        assertThat(response.getJwt()).isEqualTo("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9");
    }

    @Test
    @DisplayName("should create instance with null jwt")
    void shouldCreateInstanceWithNullJwt() {
        AuthResponse response = new AuthResponse(null);
        
        assertThat(response.getJwt()).isNull();
    }

    @Test
    @DisplayName("should create instance with empty jwt")
    void shouldCreateInstanceWithEmptyJwt() {
        AuthResponse response = new AuthResponse("");
        
        assertThat(response.getJwt()).isEmpty();
    }
}
