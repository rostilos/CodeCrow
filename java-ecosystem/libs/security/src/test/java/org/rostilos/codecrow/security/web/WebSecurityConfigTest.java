package org.rostilos.codecrow.security.web;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.security.service.UserDetailsServiceImpl;
import org.rostilos.codecrow.security.web.jwt.AuthEntryPoint;
import org.rostilos.codecrow.security.web.jwt.AuthTokenFilter;
import org.springframework.security.authentication.AuthenticationManager;
import org.springframework.security.authentication.dao.DaoAuthenticationProvider;
import org.springframework.security.config.annotation.authentication.configuration.AuthenticationConfiguration;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.web.cors.CorsConfigurationSource;
import org.springframework.web.cors.UrlBasedCorsConfigurationSource;

import java.lang.reflect.Field;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class WebSecurityConfigTest {

    @Mock
    private UserDetailsServiceImpl userDetailsService;

    @Mock
    private AuthEntryPoint unauthorizedHandler;

    @Mock
    private AuthenticationConfiguration authConfig;

    @Mock
    private AuthenticationManager authenticationManager;

    private WebSecurityConfig webSecurityConfig;

    private void setField(String fieldName, String value) throws Exception {
        Field field = WebSecurityConfig.class.getDeclaredField(fieldName);
        field.setAccessible(true);
        field.set(webSecurityConfig, value);
    }

    @BeforeEach
    void setUp() {
        webSecurityConfig = new WebSecurityConfig(userDetailsService, unauthorizedHandler);
    }

    @Test
    void testAuthenticationJwtTokenFilter_CreatesFilter() {
        AuthTokenFilter filter = webSecurityConfig.authenticationJwtTokenFilter();

        assertThat(filter).isNotNull();
    }

    @Test
    void testAuthenticationProvider_CreatesProvider() {
        DaoAuthenticationProvider provider = webSecurityConfig.authenticationProvider();

        assertThat(provider).isNotNull();
    }

    @Test
    void testAuthenticationManager_ReturnsManager() throws Exception {
        when(authConfig.getAuthenticationManager()).thenReturn(authenticationManager);

        AuthenticationManager manager = webSecurityConfig.authenticationManager(authConfig);

        assertThat(manager).isEqualTo(authenticationManager);
    }

    @Test
    void testPasswordEncoder_CreatesArgon2Encoder() {
        PasswordEncoder encoder = webSecurityConfig.passwordEncoder();

        assertThat(encoder).isNotNull();
        assertThat(encoder.getClass().getSimpleName()).contains("Argon2");
    }

    @Test
    void testTokenEncryptionService_WithKeys() throws Exception {
        // Use proper 32-byte base64 encoded keys
        setField("encryptionKey", "dGVzdC1rZXktMTIzNDU2Nzg5MDEyMzQ1Njc4OTAx");
        setField("oldEncryptionKey", "b2xkLWtleS00NTY3ODkwMTIzNDU2Nzg5MDEyMzQ1");

        TokenEncryptionService service = webSecurityConfig.tokenEncryptionService();

        assertThat(service).isNotNull();
    }

    @Test
    void testCorsConfigurationSource_CreatesConfiguration() {
        CorsConfigurationSource source = webSecurityConfig.corsConfigurationSource();

        assertThat(source).isNotNull();
        assertThat(source).isInstanceOf(UrlBasedCorsConfigurationSource.class);
    }

    @Test
    void testPasswordEncoder_CreatesInstance() {
        PasswordEncoder encoder = webSecurityConfig.passwordEncoder();

        assertThat(encoder).isNotNull();
    }
}
