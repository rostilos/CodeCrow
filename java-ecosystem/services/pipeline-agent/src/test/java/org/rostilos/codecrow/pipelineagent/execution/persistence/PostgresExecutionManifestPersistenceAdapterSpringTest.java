package org.rostilos.codecrow.pipelineagent.execution.persistence;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.analysisengine.execution.ExecutionManifestPersistencePort;
import org.springframework.aop.framework.ProxyFactory;

import javax.sql.DataSource;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;

class PostgresExecutionManifestPersistenceAdapterSpringTest {

    @Test
    void supportsSpringClassBasedRepositoryProxying() {
        ProxyFactory proxyFactory = new ProxyFactory(
                new PostgresExecutionManifestPersistenceAdapter(mock(DataSource.class)));
        proxyFactory.setProxyTargetClass(true);

        assertThat(proxyFactory.getProxy())
                .isInstanceOf(ExecutionManifestPersistencePort.class);
    }
}
