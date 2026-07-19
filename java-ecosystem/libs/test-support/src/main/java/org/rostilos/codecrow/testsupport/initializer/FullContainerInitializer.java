package org.rostilos.codecrow.testsupport.initializer;

/**
 * Combines Postgres + Redis container initialization for services
 * that depend on both (web-server, pipeline-agent).
 */
public class FullContainerInitializer extends PostgresContainerInitializer {

    @Override
    public void initialize(org.springframework.context.ConfigurableApplicationContext ctx) {
        super.initialize(ctx);
        new RedisContainerInitializer().initialize(ctx);
    }
}
