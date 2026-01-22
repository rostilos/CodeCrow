package org.rostilos.codecrow.core.model.project;

import org.junit.jupiter.api.Test;

import java.time.Instant;

import static org.assertj.core.api.Assertions.assertThat;

class ProjectTokenTest {

    @Test
    void shouldCreateProjectToken() {
        ProjectToken token = new ProjectToken();
        assertThat(token).isNotNull();
    }

    @Test
    void shouldSetAndGetId() {
        ProjectToken token = new ProjectToken();
        token.setId(100L);
        assertThat(token.getId()).isEqualTo(100L);
    }

    @Test
    void shouldSetAndGetProject() {
        ProjectToken token = new ProjectToken();
        Project project = new Project();
        
        token.setProject(project);
        
        assertThat(token.getProject()).isEqualTo(project);
    }

    @Test
    void shouldSetAndGetName() {
        ProjectToken token = new ProjectToken();
        token.setName("API Token Production");
        
        assertThat(token.getName()).isEqualTo("API Token Production");
    }

    @Test
    void shouldSetAndGetTokenEncrypted() {
        ProjectToken token = new ProjectToken();
        String encryptedValue = "AES256_encrypted_value_here";
        
        token.setTokenEncrypted(encryptedValue);
        
        assertThat(token.getTokenEncrypted()).isEqualTo(encryptedValue);
    }

    @Test
    void shouldSetAndGetCreatedAt() {
        ProjectToken token = new ProjectToken();
        Instant now = Instant.now();
        
        token.setCreatedAt(now);
        
        assertThat(token.getCreatedAt()).isEqualTo(now);
    }

    @Test
    void shouldSetAndGetExpiresAt() {
        ProjectToken token = new ProjectToken();
        Instant expiration = Instant.now().plusSeconds(86400);
        
        token.setExpiresAt(expiration);
        
        assertThat(token.getExpiresAt()).isEqualTo(expiration);
    }

    @Test
    void shouldHandleNullExpiresAt() {
        ProjectToken token = new ProjectToken();
        token.setExpiresAt(null);
        
        assertThat(token.getExpiresAt()).isNull();
    }

    @Test
    void shouldChainSetters() {
        Instant now = Instant.now();
        Instant expiration = now.plusSeconds(3600);
        Project project = new Project();
        
        ProjectToken token = new ProjectToken()
                .setId(1L)
                .setName("Test Token")
                .setTokenEncrypted("encrypted")
                .setCreatedAt(now)
                .setExpiresAt(expiration)
                .setProject(project);
        
        assertThat(token.getId()).isEqualTo(1L);
        assertThat(token.getName()).isEqualTo("Test Token");
        assertThat(token.getTokenEncrypted()).isEqualTo("encrypted");
        assertThat(token.getCreatedAt()).isEqualTo(now);
        assertThat(token.getExpiresAt()).isEqualTo(expiration);
        assertThat(token.getProject()).isEqualTo(project);
    }
}
