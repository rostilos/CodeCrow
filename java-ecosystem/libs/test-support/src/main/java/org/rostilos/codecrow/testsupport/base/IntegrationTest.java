package org.rostilos.codecrow.testsupport.base;

import org.junit.jupiter.api.TestInstance;
import org.rostilos.codecrow.testsupport.initializer.PostgresContainerInitializer;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.ContextConfiguration;

import java.lang.annotation.*;

/**
 * Meta-annotation for JPA/Repository integration tests.
 * <p>
 * Injects the guarded PostgreSQL endpoint, creates schema on context start,
 * and activates the "it" profile.
 */
@Target(ElementType.TYPE)
@Retention(RetentionPolicy.RUNTIME)
@Inherited
@ActiveProfiles("it")
@ContextConfiguration(initializers = PostgresContainerInitializer.class)
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
public @interface IntegrationTest {
}
