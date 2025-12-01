package org.rostilos.codecrow.security.pipelineagent;

import jakarta.servlet.DispatcherType;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.security.jwt.utils.JwtUtils;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.security.pipelineagent.jwt.ProjectInternalJwtFilter;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.authentication.AuthenticationManager;
import org.springframework.security.config.annotation.authentication.configuration.AuthenticationConfiguration;
import org.springframework.security.config.annotation.method.configuration.EnableMethodSecurity;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.UsernamePasswordAuthenticationFilter;

@Configuration
@EnableMethodSecurity(securedEnabled = true, jsr250Enabled = true)
public class PipelineAgentSecurityConfig {
    @Value("${codecrow.security.encryption-key}")
    private String encryptionKey;
    private final JwtUtils jwtUtils;
    private final ProjectRepository projectRepository;

    public PipelineAgentSecurityConfig(
            JwtUtils jwtUtils,
            ProjectRepository projectRepository
    ) {
        this.jwtUtils = jwtUtils;
        this.projectRepository = projectRepository;
    }

    @Bean
    public AuthenticationManager authenticationManager(AuthenticationConfiguration authConfig) throws Exception {
        return authConfig.getAuthenticationManager();
    }

    @Bean
    public PasswordEncoder passwordEncoder() {
        return new BCryptPasswordEncoder();
    }

    @Bean
    public TokenEncryptionService tokenEncryptionService() {
        return new TokenEncryptionService(encryptionKey);
    }

    @Bean
    public ProjectInternalJwtFilter internalJwtFilter() {
        return new ProjectInternalJwtFilter(jwtUtils, projectRepository, "/actuator/health", "/api/webhooks/");
    }

    @Bean
    public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
        http.csrf(csrf -> csrf.disable())
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .authorizeHttpRequests(auth ->
                        auth.requestMatchers("/actuator/health").permitAll()
                             .requestMatchers("/api/webhooks/**").permitAll()
                             .dispatcherTypeMatchers(DispatcherType.ASYNC).permitAll()
                             .requestMatchers("/api/test/**").authenticated()
                             .requestMatchers("/actuator/**").authenticated()
                             .anyRequest().authenticated()
                );

        // Register internal project-level JWT validator before username/password filter
        http.addFilterBefore(internalJwtFilter(), UsernamePasswordAuthenticationFilter.class);

        return http.build();
    }
}
