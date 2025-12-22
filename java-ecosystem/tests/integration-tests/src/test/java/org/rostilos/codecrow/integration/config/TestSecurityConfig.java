package org.rostilos.codecrow.integration.config;

import org.rostilos.codecrow.security.web.jwt.AuthEntryPoint;
import org.rostilos.codecrow.security.web.jwt.AuthTokenFilter;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Primary;
import org.springframework.security.authentication.AuthenticationProvider;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configurers.AbstractHttpConfigurer;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.UsernamePasswordAuthenticationFilter;
import org.springframework.web.cors.CorsConfiguration;
import org.springframework.web.cors.UrlBasedCorsConfigurationSource;

import java.util.Arrays;

/**
 * Test security configuration that extends the base security config.
 * Adds /error endpoint to permit list so we can see actual error messages in tests.
 */
@TestConfiguration
public class TestSecurityConfig {

    private final AuthEntryPoint unauthorizedHandler;
    private final AuthTokenFilter authenticationJwtTokenFilter;
    private final AuthenticationProvider authenticationProvider;

    public TestSecurityConfig(AuthEntryPoint unauthorizedHandler,
                              AuthTokenFilter authenticationJwtTokenFilter,
                              AuthenticationProvider authenticationProvider) {
        this.unauthorizedHandler = unauthorizedHandler;
        this.authenticationJwtTokenFilter = authenticationJwtTokenFilter;
        this.authenticationProvider = authenticationProvider;
    }

    @Bean
    public UrlBasedCorsConfigurationSource testCorsConfigurationSource() {
        CorsConfiguration configuration = new CorsConfiguration();
        configuration.setAllowedOriginPatterns(Arrays.asList("*"));
        configuration.setAllowedMethods(Arrays.asList("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"));
        configuration.setAllowedHeaders(Arrays.asList("*"));
        configuration.setAllowCredentials(true);
        configuration.setMaxAge(3600L);

        UrlBasedCorsConfigurationSource source = new UrlBasedCorsConfigurationSource();
        source.registerCorsConfiguration("/**", configuration);
        return source;
    }

    @Bean
    @Primary
    public SecurityFilterChain testFilterChain(HttpSecurity http) throws Exception {
        http.csrf(AbstractHttpConfigurer::disable)
                .cors(cors -> cors.configurationSource(testCorsConfigurationSource()))
                .exceptionHandling(exception -> exception.authenticationEntryPoint(unauthorizedHandler))
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .authorizeHttpRequests(auth ->
                        auth
                                // Allow all OPTIONS requests (CORS preflight)
                                .requestMatchers(org.springframework.http.HttpMethod.OPTIONS, "/**").permitAll()
                                .requestMatchers("/api/auth/**").permitAll()
                                .requestMatchers("/api/test/**").permitAll()
                                // Allow /error to see actual error messages
                                .requestMatchers("/error").permitAll()
                                .requestMatchers("/error/**").permitAll()
                                // OAuth callbacks need to be public
                                .requestMatchers("/api/*/integrations/*/app/callback").permitAll()
                                .requestMatchers("/api/integrations/*/app/callback").permitAll()
                                .requestMatchers("/actuator/**").permitAll()
                                .requestMatchers("/internal/projects/**").permitAll()
                                .requestMatchers("/swagger-ui-custom.html").permitAll()
                                .requestMatchers("/api-docs").permitAll()
                                .anyRequest().authenticated()
                );

        http.authenticationProvider(authenticationProvider);
        http.addFilterBefore(authenticationJwtTokenFilter, UsernamePasswordAuthenticationFilter.class);

        return http.build();
    }
}
