package org.rostilos.codecrow.publicshare.config;

import org.rostilos.codecrow.publicshare.persistence.PublicShareLinkRepository;
import org.rostilos.codecrow.publicshare.service.PublicShareLinkService;
import org.springframework.boot.autoconfigure.AutoConfiguration;
import org.springframework.boot.autoconfigure.domain.EntityScan;
import org.springframework.context.annotation.Bean;
import org.springframework.data.jpa.repository.config.EnableJpaRepositories;

/**
 * Self-contained Spring Boot integration for opaque public-share credentials.
 * Applications opt in by depending on this artifact and do not scan its
 * entity, repository, or service implementation packages themselves.
 */
@AutoConfiguration
@EnableJpaRepositories(basePackages = "org.rostilos.codecrow.publicshare.persistence")
@EntityScan(basePackages = "org.rostilos.codecrow.publicshare.model")
public class PublicShareAutoConfiguration {

    @Bean
    public PublicShareLinkService publicShareLinkService(PublicShareLinkRepository repository) {
        return new PublicShareLinkService(repository);
    }
}
