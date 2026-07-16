package org.rostilos.codecrow.pipelineagent.execution;

import org.rostilos.codecrow.analysisengine.coverage.CoverageLedgerPersistencePort;
import org.rostilos.codecrow.analysisengine.coverage.CoverageLedgerService;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/** Production wiring for the durable exact-diff coverage ledger. */
@Configuration(proxyBeanMethods = false)
public class CoverageLedgerConfiguration {

    @Bean
    public CoverageLedgerService coverageLedgerService(
            CoverageLedgerPersistencePort persistencePort) {
        return new CoverageLedgerService(persistencePort);
    }
}
