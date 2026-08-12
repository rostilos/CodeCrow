package org.rostilos.codecrow.publicshare.config;

import org.junit.jupiter.api.Test;
import org.springframework.boot.autoconfigure.AutoConfiguration;
import org.springframework.boot.autoconfigure.domain.EntityScan;
import org.springframework.data.jpa.repository.config.EnableJpaRepositories;

import static org.assertj.core.api.Assertions.assertThat;

class PublicShareAutoConfigurationTest {

    @Test
    void ownsItsSpringIntegrationBoundaries() {
        assertThat(PublicShareAutoConfiguration.class)
                .hasAnnotation(AutoConfiguration.class);

        EnableJpaRepositories repositories = PublicShareAutoConfiguration.class
                .getAnnotation(EnableJpaRepositories.class);
        assertThat(repositories.basePackages())
                .containsExactly("org.rostilos.codecrow.publicshare.persistence");

        EntityScan entities = PublicShareAutoConfiguration.class
                .getAnnotation(EntityScan.class);
        assertThat(entities.basePackages())
                .containsExactly("org.rostilos.codecrow.publicshare.model");
    }
}
