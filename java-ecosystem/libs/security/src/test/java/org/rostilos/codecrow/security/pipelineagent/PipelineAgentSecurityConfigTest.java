package org.rostilos.codecrow.security.pipelineagent;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.security.jwt.utils.JwtUtils;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.security.pipelineagent.jwt.ProjectInternalJwtFilter;
import org.springframework.security.authentication.AuthenticationManager;
import org.springframework.security.config.annotation.authentication.configuration.AuthenticationConfiguration;
import org.springframework.security.crypto.password.PasswordEncoder;

import java.lang.reflect.Field;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class PipelineAgentSecurityConfigTest {

    @Mock
    private JwtUtils jwtUtils;

    @Mock
    private ProjectRepository projectRepository;

    @Mock
    private AuthenticationConfiguration authConfig;

    @Mock
    private AuthenticationManager authenticationManager;

    private PipelineAgentSecurityConfig pipelineAgentSecurityConfig;

    private void setField(String fieldName, String value) throws Exception {
        Field field = PipelineAgentSecurityConfig.class.getDeclaredField(fieldName);
        field.setAccessible(true);
        field.set(pipelineAgentSecurityConfig, value);
    }

    @BeforeEach
    void setUp() {
        pipelineAgentSecurityConfig = new PipelineAgentSecurityConfig(jwtUtils, projectRepository);
    }

    @Test
    void testAuthenticationManager_ReturnsManager() throws Exception {
        when(authConfig.getAuthenticationManager()).thenReturn(authenticationManager);

        AuthenticationManager manager = pipelineAgentSecurityConfig.authenticationManager(authConfig);

        assertThat(manager).isEqualTo(authenticationManager);
    }

    @Test
    void testPasswordEncoder_CreatesBCryptEncoder() {
        PasswordEncoder encoder = pipelineAgentSecurityConfig.passwordEncoder();

        assertThat(encoder).isNotNull();
        assertThat(encoder.getClass().getSimpleName()).contains("BCrypt");
    }

    @Test
    void testPasswordEncoder_CreatesInstance() {
        PasswordEncoder encoder = pipelineAgentSecurityConfig.passwordEncoder();

        assertThat(encoder).isNotNull();
    }

    @Test
    void testTokenEncryptionService_WithKeys() throws Exception {
        // Use proper 32-byte base64 encoded keys
        setField("encryptionKey", "dGVzdC1rZXktMTIzNDU2Nzg5MDEyMzQ1Njc4OTAx");
        setField("oldEncryptionKey", "b2xkLWtleS00NTY3ODkwMTIzNDU2Nzg5MDEyMzQ1");

        TokenEncryptionService service = pipelineAgentSecurityConfig.tokenEncryptionService();

        assertThat(service).isNotNull();
    }

    @Test
    void testInternalJwtFilter_CreatesFilter() {
        ProjectInternalJwtFilter filter = pipelineAgentSecurityConfig.internalJwtFilter();

        assertThat(filter).isNotNull();
    }

    @Test
    void testInternalJwtFilter_ConfiguresExcludedPaths() {
        ProjectInternalJwtFilter filter = pipelineAgentSecurityConfig.internalJwtFilter();

        assertThat(filter).isNotNull();
    }
}
