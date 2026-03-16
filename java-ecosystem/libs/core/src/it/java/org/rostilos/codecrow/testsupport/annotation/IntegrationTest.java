package org.rostilos.codecrow.testsupport.annotation;

import org.rostilos.codecrow.testsupport.initializer.PostgresContainerInitializer;
import org.junit.jupiter.api.TestInstance;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.ContextConfiguration;

import java.lang.annotation.*;

/**
 * Meta-annotation for JPA/Repository integration tests.
 * Local copy — core cannot depend on test-support (cyclic dependency).
 */
@Target(ElementType.TYPE)
@Retention(RetentionPolicy.RUNTIME)
@Inherited
@ActiveProfiles("it")
@ContextConfiguration(initializers = PostgresContainerInitializer.class)
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
public @interface IntegrationTest {
}
