package org.rostilos.codecrow.security.web;

import jakarta.servlet.DispatcherType;
import java.util.Arrays;
import org.rostilos.codecrow.security.web.jwt.AuthEntryPoint;
import org.rostilos.codecrow.security.web.jwt.AuthTokenFilter;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.security.service.UserDetailsServiceImpl;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.authentication.AuthenticationManager;
import org.springframework.security.authentication.dao.DaoAuthenticationProvider;
import org.springframework.security.config.annotation.authentication.configuration.AuthenticationConfiguration;
import org.springframework.security.config.annotation.method.configuration.EnableMethodSecurity;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configurers.AbstractHttpConfigurer;
import org.springframework.security.config.annotation.web.configurers.HeadersConfigurer;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.crypto.argon2.Argon2PasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.UsernamePasswordAuthenticationFilter;
import org.springframework.web.cors.CorsConfiguration;
import org.springframework.web.cors.CorsConfigurationSource;
import org.springframework.web.cors.UrlBasedCorsConfigurationSource;

@Configuration
@EnableMethodSecurity(securedEnabled = true, jsr250Enabled = true)
public class WebSecurityConfig {
    @Value("${codecrow.security.encryption-key}")
    private String encryptionKey;

    @Value("${codecrow.security.encryption-key-old}")
    private String oldEncryptionKey;

    @Value("${codecrow.internal.api.secret:}")
    private String internalApiSecret;

    private final UserDetailsServiceImpl userDetailsService;
    private final AuthEntryPoint unauthorizedHandler;

    public WebSecurityConfig(
            UserDetailsServiceImpl userDetailsService,
            AuthEntryPoint unauthorizedHandler) {
        this.userDetailsService = userDetailsService;
        this.unauthorizedHandler = unauthorizedHandler;
    }

    @Bean
    public AuthTokenFilter authenticationJwtTokenFilter() {
        return new AuthTokenFilter();
    }

    @Bean
    public DaoAuthenticationProvider authenticationProvider() {
        DaoAuthenticationProvider authProvider = new DaoAuthenticationProvider();

        authProvider.setUserDetailsService(userDetailsService);
        authProvider.setPasswordEncoder(passwordEncoder());

        return authProvider;
    }

    @Bean
    public AuthenticationManager authenticationManager(AuthenticationConfiguration authConfig) throws Exception {
        return authConfig.getAuthenticationManager();
    }

    @Bean
    public PasswordEncoder passwordEncoder() {
        return Argon2PasswordEncoder.defaultsForSpringSecurity_v5_8();
    }

    @Bean
    public TokenEncryptionService tokenEncryptionService() {
        return new TokenEncryptionService(encryptionKey, oldEncryptionKey);
    }

    @Bean
    public CorsConfigurationSource corsConfigurationSource() {
        CorsConfiguration configuration = new CorsConfiguration();

        // TODO: replace Arrays.asList("*") with the explicit domain(s)
        // TODO: Example: Arrays.asList("http://localhost:8080",
        // "https://frontend.rostilos.pp.ua")
        configuration.setAllowedOriginPatterns(Arrays.asList("*"));

        configuration.setAllowedMethods(Arrays.asList("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"));
        configuration.setAllowedHeaders(Arrays.asList("*"));
        configuration.setAllowCredentials(true);
        configuration.setMaxAge(3600L); // Max age for preflight response

        UrlBasedCorsConfigurationSource source = new UrlBasedCorsConfigurationSource();
        source.registerCorsConfiguration("/**", configuration); // Apply this configuration to all paths
        return source;
    }
    // END: ADDED CORS CONFIGURATION BEAN

    @Bean
    public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
        http.csrf(AbstractHttpConfigurer::disable)
                .cors(cors -> cors.configurationSource(corsConfigurationSource()))
                .exceptionHandling(exception -> exception.authenticationEntryPoint(unauthorizedHandler))
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                // Allow framing from Bitbucket for Connect App configure page
                .headers(headers -> headers
                        .frameOptions(HeadersConfigurer.FrameOptionsConfig::disable)
                        .contentSecurityPolicy(csp -> csp
                                .policyDirectives(
                                        "frame-ancestors 'self' https://bitbucket.org https://*.bitbucket.org")))
                .authorizeHttpRequests(auth -> auth
                        // Allow async dispatches to complete (SSE, streaming responses)
                        .dispatcherTypeMatchers(DispatcherType.ASYNC, DispatcherType.ERROR).permitAll()
                        // Allow all OPTIONS requests (CORS preflight)
                        .requestMatchers(org.springframework.http.HttpMethod.OPTIONS, "/**").permitAll()
                        // Allow error page to be rendered without authentication
                        .requestMatchers("/error").permitAll()
                        .requestMatchers("/api/auth/**").permitAll()
                        .requestMatchers("/api/test/**").permitAll()
                        // Public site configuration (runtime feature detection)
                        .requestMatchers("/api/public/**").permitAll()
                        // OAuth callbacks need to be public (called by VCS providers)
                        .requestMatchers("/api/*/integrations/*/app/callback").permitAll()
                        // Generic OAuth callbacks without workspace slug (for GitHub, etc.)
                        .requestMatchers("/api/integrations/*/app/callback").permitAll()
                        .requestMatchers("/actuator/**").permitAll()
                        .requestMatchers("/internal/projects/**").permitAll()
                        .requestMatchers("/api/internal/**").permitAll()
                        .requestMatchers("/swagger-ui-custom.html").permitAll()
                        .requestMatchers("/api-docs").permitAll()
                        // Bitbucket Connect App lifecycle callbacks (uses JWT auth)
                        .requestMatchers("/api/bitbucket/connect/descriptor").permitAll()
                        .requestMatchers("/api/bitbucket/connect/installed").permitAll()
                        .requestMatchers("/api/bitbucket/connect/uninstalled").permitAll()
                        .requestMatchers("/api/bitbucket/connect/enabled").permitAll()
                        .requestMatchers("/api/bitbucket/connect/disabled").permitAll()
                        .requestMatchers("/api/bitbucket/connect/status").permitAll()
                        .requestMatchers("/api/bitbucket/connect/configure").permitAll()
                        // Stripe webhooks (authenticate via signature verification, not JWT)
                        .requestMatchers("/api/webhooks/**").permitAll()
                        .anyRequest().authenticated());

        http.authenticationProvider(authenticationProvider());

        http.addFilterBefore(authenticationJwtTokenFilter(), UsernamePasswordAuthenticationFilter.class);

        return http.build();
    }

}
