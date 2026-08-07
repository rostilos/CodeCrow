package org.rostilos.codecrow.scmevidence.config;

import org.springframework.boot.autoconfigure.AutoConfiguration;
import org.springframework.boot.autoconfigure.domain.EntityScan;
import org.springframework.context.annotation.ComponentScan;
import org.springframework.data.jpa.repository.config.EnableJpaRepositories;

/**
 * Self-contained Spring Boot integration for the provider-neutral SCM evidence
 * package. Applications opt in by including the package artifact; they do not
 * need to know its internal service, persistence, or entity package layout.
 */
@AutoConfiguration
@ComponentScan(basePackages = "org.rostilos.codecrow.scmevidence.service")
@EnableJpaRepositories(basePackages = "org.rostilos.codecrow.scmevidence.persistence")
@EntityScan(basePackages = "org.rostilos.codecrow.scmevidence.model")
public class ScmEvidenceAutoConfiguration {
}
