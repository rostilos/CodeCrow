package org.rostilos.codecrow.pipelineagent;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.autoconfigure.domain.EntityScan;
import org.springframework.data.jpa.repository.config.EnableJpaRepositories;
import org.springframework.scheduling.annotation.EnableAsync;
import org.springframework.scheduling.annotation.EnableScheduling;

@SpringBootApplication(scanBasePackages = {
        "org.rostilos.codecrow.pipelineagent",
        "org.rostilos.codecrow.core.service",
        "org.rostilos.codecrow.vcsclient",
        "org.rostilos.codecrow.security.jwt",
        "org.rostilos.codecrow.security.pipelineagent",
        "org.rostilos.codecrow.analysisengine",
        "org.rostilos.codecrow.ragengine"
})
@EnableJpaRepositories(basePackages = "org.rostilos.codecrow.core.persistence.repository")
@EntityScan(basePackages = "org.rostilos.codecrow.core.model")
@EnableScheduling
@EnableAsync
public class ProcessingApplication {

    public static void main(String[] args) {

        SpringApplication.run(ProcessingApplication.class, args);
    }
}
