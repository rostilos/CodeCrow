package org.rostilos.codecrow.pipelineagent.execution;

import org.rostilos.codecrow.analysisengine.execution.ExecutionManifestPersistencePort;
import org.rostilos.codecrow.analysisengine.execution.ExecutionManifestService;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/** Production wiring for the durable immutable execution-manifest boundary. */
@Configuration(proxyBeanMethods = false)
public class ExecutionManifestConfiguration {
    @Bean
    public ExecutionManifestService executionManifestService(
            ExecutionManifestPersistencePort persistencePort) {
        return new ExecutionManifestService(persistencePort);
    }
}
