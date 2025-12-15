package org.rostilos.codecrow.integration;

import org.rostilos.codecrow.webserver.WebserverApplication;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.autoconfigure.domain.EntityScan;
import org.springframework.context.annotation.ComponentScan;
import org.springframework.context.annotation.FilterType;
import org.springframework.data.jpa.repository.config.EnableJpaAuditing;
import org.springframework.data.jpa.repository.config.EnableJpaRepositories;
import org.springframework.scheduling.annotation.EnableAsync;

/**
 * Test application for integration tests.
 * Mirrors the production WebserverApplication component scan configuration.
 */
@SpringBootApplication(scanBasePackages = "org.rostilos.codecrow.integration")
@EnableJpaAuditing
@EnableAsync
@EntityScan(basePackages = {
        "org.rostilos.codecrow.core.model"
})
@EnableJpaRepositories(basePackages = {
        "org.rostilos.codecrow.core.persistence.repository"
})
@ComponentScan(
        basePackages = {
                "org.rostilos.codecrow.webserver",
                "org.rostilos.codecrow.vcsclient",
                "org.rostilos.codecrow.core",
                "org.rostilos.codecrow.security.jwt.utils",
                "org.rostilos.codecrow.security.web",
                "org.rostilos.codecrow.security.service",
                "org.rostilos.codecrow.security.oauth",
                "org.rostilos.codecrow.email",
                "org.rostilos.codecrow.integration"
        },
        excludeFilters = @ComponentScan.Filter(
                type = FilterType.ASSIGNABLE_TYPE,
                classes = {WebserverApplication.class}
        )
)
public class IntegrationTestApplication {
    
    public static void main(String[] args) {
        SpringApplication.run(IntegrationTestApplication.class, args);
    }
}
