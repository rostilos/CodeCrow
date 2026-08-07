package org.rostilos.codecrow.scmevidence.config;

import org.junit.jupiter.api.Test;
import org.springframework.boot.autoconfigure.AutoConfiguration;
import org.springframework.boot.autoconfigure.domain.EntityScan;
import org.springframework.context.annotation.ComponentScan;
import org.springframework.data.jpa.repository.config.EnableJpaRepositories;

import static org.assertj.core.api.Assertions.assertThat;

class ScmEvidenceAutoConfigurationTest {

    @Test
    void ownsItsSpringIntegrationBoundaries() {
        assertThat(ScmEvidenceAutoConfiguration.class)
                .hasAnnotation(AutoConfiguration.class);

        ComponentScan componentScan = ScmEvidenceAutoConfiguration.class
                .getAnnotation(ComponentScan.class);
        assertThat(componentScan.basePackages())
                .containsExactly("org.rostilos.codecrow.scmevidence.service");

        EnableJpaRepositories repositories = ScmEvidenceAutoConfiguration.class
                .getAnnotation(EnableJpaRepositories.class);
        assertThat(repositories.basePackages())
                .containsExactly("org.rostilos.codecrow.scmevidence.persistence");

        EntityScan entities = ScmEvidenceAutoConfiguration.class
                .getAnnotation(EntityScan.class);
        assertThat(entities.basePackages())
                .containsExactly("org.rostilos.codecrow.scmevidence.model");
    }
}
