package org.rostilos.codecrow.pipelineagent.execution.persistence;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;

import javax.sql.DataSource;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.analysisengine.coverage.CoverageLedgerPersistencePort;
import org.springframework.aop.framework.ProxyFactory;

/** Wiring contract for the JDBC-backed VS-05 coverage persistence port. */
class PostgresCoverageLedgerPersistenceAdapterSpringTest {

    @Test
    void supportsSpringClassBasedRepositoryProxyingThroughTheCoveragePort() {
        ProxyFactory proxyFactory = new ProxyFactory(
                new PostgresCoverageLedgerPersistenceAdapter(mock(DataSource.class)));
        proxyFactory.setProxyTargetClass(true);

        assertThat(proxyFactory.getProxy())
                .isInstanceOf(CoverageLedgerPersistencePort.class);
    }
}
