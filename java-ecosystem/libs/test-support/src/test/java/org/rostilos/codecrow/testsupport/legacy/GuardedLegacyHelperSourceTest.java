package org.rostilos.codecrow.testsupport.legacy;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.testsupport.initializer.FullContainerInitializer;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class GuardedLegacyHelperSourceTest {

    @Test
    void guardedHelpersContainNoInJvmContainerStartupOrGlobalPropertyMutation() throws IOException {
        Path javaRoot = javaRoot();
        List<Path> guardedHelpers = List.of(
                javaRoot.resolve("libs/test-support/src/main/java/org/rostilos/codecrow/"
                        + "testsupport/containers/SharedPostgresContainer.java"),
                javaRoot.resolve("libs/test-support/src/main/java/org/rostilos/codecrow/"
                        + "testsupport/containers/SharedRedisContainer.java"),
                javaRoot.resolve("libs/core/src/it/java/org/rostilos/codecrow/"
                        + "testsupport/containers/SharedPostgresContainer.java")
        );

        for (Path helper : guardedHelpers) {
            String source = Files.readString(helper);
            assertThat(source)
                    .as(helper.toString())
                    .doesNotContain(
                            "org.testcontainers",
                            "PostgreSQLContainer",
                            "GenericContainer",
                            "DockerClientFactory",
                            ".withReuse(",
                            ".start()",
                            "System.setProperty("
                    );
        }
    }

    @Test
    void guardedInitializersInjectOnlyIntoTheirApplicationContext() throws IOException {
        Path javaRoot = javaRoot();
        for (Path initializer : List.of(
                javaRoot.resolve("libs/test-support/src/main/java/org/rostilos/codecrow/"
                        + "testsupport/initializer/PostgresContainerInitializer.java"),
                javaRoot.resolve("libs/test-support/src/main/java/org/rostilos/codecrow/"
                        + "testsupport/initializer/RedisContainerInitializer.java"),
                javaRoot.resolve("libs/core/src/it/java/org/rostilos/codecrow/"
                        + "testsupport/initializer/PostgresContainerInitializer.java")
        )) {
            String source = Files.readString(initializer);
            assertThat(source)
                    .as(initializer.toString())
                    .contains("TestPropertyValues.of(")
                    .contains(".applyTo(ctx);")
                    .doesNotContain("System.setProperty(", "applySystemProperties(");
        }
    }

    @Test
    void guardedRuntimeHasNoContainerClientDependency() throws IOException {
        Path packageDirectory = javaRoot().resolve(
                "libs/test-support/src/main/java/org/rostilos/codecrow/testsupport/legacy"
        );
        try (var sources = Files.list(packageDirectory)) {
            for (Path source : sources.filter(path -> path.toString().endsWith(".java")).toList()) {
                assertThat(Files.readString(source))
                        .as(source.toString())
                        .doesNotContain(
                                "org.testcontainers",
                                "com.github.dockerjava",
                                "DockerClientFactory",
                                "PostgreSQLContainer",
                                "GenericContainer"
                        );
            }
        }
    }

    @Test
    void containerLibrariesAreOptionalAndAbsentFromTheCoreModule() throws IOException {
        String pom = Files.readString(javaRoot().resolve("libs/test-support/pom.xml"));
        Matcher dependencies = Pattern.compile(
                "<dependency>\\s*<groupId>org\\.testcontainers</groupId>"
                        + ".*?<optional>true</optional>\\s*</dependency>",
                Pattern.DOTALL
        ).matcher(pom);
        assertThat(dependencies.results().count()).isEqualTo(3);
        assertThat(Files.readString(javaRoot().resolve("libs/core/pom.xml")))
                .doesNotContain("<groupId>org.testcontainers</groupId>");
    }

    @Test
    void combinedContainerInitializerFailsClosedInsteadOfMixingLaneContracts() {
        assertThatThrownBy(() -> new FullContainerInitializer().initialize(null))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("not a valid guarded lane");
    }

    @Test
    void serviceRegistrationAndAbstractBasesBindOnlyLiteralLoopback() throws IOException {
        Path javaRoot = javaRoot();
        Path service = javaRoot.resolve(
                "libs/test-support/src/main/resources/META-INF/services/"
                        + "org.junit.platform.launcher.LauncherSessionListener"
        );
        assertThat(Files.readString(service).strip()).isEqualTo(
                "org.rostilos.codecrow.testsupport.legacy."
                        + "LegacyContainerItLauncherSessionListener"
        );

        for (Path base : List.of(
                javaRoot.resolve("services/pipeline-agent/src/it/java/org/rostilos/codecrow/"
                        + "pipelineagent/BasePipelineAgentIT.java"),
                javaRoot.resolve("services/web-server/src/it/java/org/rostilos/codecrow/"
                        + "webserver/BaseWebServerIT.java")
        )) {
            String source = Files.readString(base);
            assertThat(source)
                    .contains("RestAssured.baseURI = \"http://127.0.0.1\";")
                    .contains("LegacyContainerItSession.registerApplicationLoopback(port);")
                    .doesNotContain("@DirtiesContext")
                    .doesNotContain("http://localhost");
        }
    }

    private static Path javaRoot() {
        Path current = Path.of(System.getProperty("user.dir")).toAbsolutePath().normalize();
        while (current != null && !Files.isDirectory(current.resolve("java-ecosystem"))) {
            current = current.getParent();
        }
        if (current == null) {
            throw new IllegalStateException("cannot locate repository root");
        }
        return current.resolve("java-ecosystem");
    }
}
