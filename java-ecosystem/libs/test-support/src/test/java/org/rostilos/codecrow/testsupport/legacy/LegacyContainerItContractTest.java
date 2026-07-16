package org.rostilos.codecrow.testsupport.legacy;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.mockito.MockedStatic;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermissions;
import java.nio.file.attribute.UserPrincipal;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.CALLS_REAL_METHODS;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.mockStatic;

class LegacyContainerItContractTest {

    @TempDir
    Path ledgerDirectory;

    @Test
    void anEmptyEnvironmentLeavesTheListenerInactive() {
        assertThat(LegacyContainerItContract.activation(Map.of(), Map.of())).isEmpty();
        assertThat(LegacyContainerItContract.activation(
                Map.of("DOCKER_HOST", "unix:///run/docker.sock"),
                Map.of()
        )).isEmpty();
    }

    @Test
    void parsesTheExactQueueContractAndExposesATestcontainersShapedFacade() {
        Map<String, String> environment = queueEnvironment();
        Map<String, String> properties = queueProperties();

        LegacyContainerItContract.Activation activation = LegacyContainerItContract
                .activation(environment, properties)
                .orElseThrow();

        assertThat(activation.protocol()).isEqualTo("1");
        assertThat(activation.runId()).isEqualTo("p007_queue_01234567");
        assertThat(activation.lane()).isEqualTo(LegacyContainerItContract.Lane.QUEUE);
        assertThat(activation.targetArtifact()).isEqualTo("codecrow-queue");
        assertThat(activation.postgres()).isEmpty();
        LegacyContainerEndpoints.RedisEndpoint redis = activation.redis().orElseThrow();
        assertThat(redis.getHost()).isEqualTo("127.0.0.1");
        assertThat(redis.getMappedPort(6379)).isEqualTo(16379);
        assertThatThrownBy(() -> redis.getMappedPort(6380))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("6379");
        assertThat(activation.ledgerPath().getFileName().toString())
                .isEqualTo("legacy-container-it-queue-p007_queue_01234567.json");
    }

    @Test
    void parsesTheExactPostgresContractWithoutAcceptingMutableEndpointShapes() {
        for (LegacyContainerItContract.Lane lane : new LegacyContainerItContract.Lane[]{
                LegacyContainerItContract.Lane.PIPELINE,
                LegacyContainerItContract.Lane.WEB
        }) {
            Map<String, String> environment = postgresEnvironment(lane);
            Map<String, String> properties = propertiesFor(lane);

            LegacyContainerEndpoints.PostgresEndpoint postgres = LegacyContainerItContract
                    .activation(environment, properties)
                    .orElseThrow()
                    .postgres()
                    .orElseThrow();

            assertThat(postgres.host()).isEqualTo("127.0.0.1");
            assertThat(postgres.port()).isEqualTo(15432);
            assertThat(postgres.jdbcUrl())
                    .isEqualTo("jdbc:postgresql://127.0.0.1:15432/p007_acceptance");
            assertThat(postgres.springProperties()).containsExactly(
                    "spring.datasource.url=jdbc:postgresql://127.0.0.1:15432/p007_acceptance",
                    "spring.datasource.username=offline_fixture",
                    "spring.datasource.password=offline_fixture_only",
                    "spring.datasource.driver-class-name=org.postgresql.Driver"
            );
        }
    }

    @Test
    void freezesTheReviewedModuleAndSelectorContracts() {
        assertThat(LegacyContainerItContract.Lane.QUEUE.targetArtifact())
                .isEqualTo("codecrow-queue");
        assertThat(LegacyContainerItContract.Lane.QUEUE.selectors()).isEqualTo(
                "org.rostilos.codecrow.queue.ConnectionFactoryIT,"
                        + "org.rostilos.codecrow.queue.QueueIsolationIT,"
                        + "org.rostilos.codecrow.queue.RedisQueueIT"
        );
        assertThat(LegacyContainerItContract.Lane.PIPELINE.targetArtifact())
                .isEqualTo("codecrow-pipeline-agent");
        assertThat(LegacyContainerItContract.Lane.PIPELINE.selectors()).isEqualTo(
                "org.rostilos.codecrow.pipelineagent.BranchResolverFlowIT,"
                        + "org.rostilos.codecrow.pipelineagent.ExecutionManifestPersistenceIT,"
                        + "org.rostilos.codecrow.pipelineagent.HealthCheckControllerIT,"
                        + "org.rostilos.codecrow.pipelineagent.LineTrackingFlowIT,"
                        + "org.rostilos.codecrow.pipelineagent.PipelineActionControllerIT,"
                        + "org.rostilos.codecrow.pipelineagent.PipelineAgentSecurityIT,"
                        + "org.rostilos.codecrow.pipelineagent.ProviderWebhookControllerIT,"
                        + "org.rostilos.codecrow.pipelineagent.RagIndexingControllerIT"
        );
        assertThat(LegacyContainerItContract.Lane.WEB.targetArtifact())
                .isEqualTo("codecrow-web-server");
        assertThat(LegacyContainerItContract.Lane.WEB.selectors()).isEqualTo(
                "org.rostilos.codecrow.webserver.AuthControllerIT,"
                        + "org.rostilos.codecrow.webserver.HealthCheckControllerIT,"
                        + "org.rostilos.codecrow.webserver.InternalApiSecurityIT,"
                        + "org.rostilos.codecrow.webserver.LlmModelControllerIT,"
                        + "org.rostilos.codecrow.webserver.ManagedImmutableManifestFlywayIT,"
                        + "org.rostilos.codecrow.webserver.ProjectControllerIT,"
                        + "org.rostilos.codecrow.webserver.PublicSiteConfigControllerIT,"
                        + "org.rostilos.codecrow.webserver.QualityGateControllerIT,"
                        + "org.rostilos.codecrow.webserver.TaskManagementControllerIT,"
                        + "org.rostilos.codecrow.webserver.UserDataControllerIT,"
                        + "org.rostilos.codecrow.webserver.WorkspaceControllerIT"
        );
    }

    @Test
    void rejectsRelativeMissingNonDirectorySymlinkedAndUntrustedLedgerDirectories()
            throws Exception {
        Map<String, String> relative = queueEnvironment();
        relative.put(LegacyContainerItContract.LEDGER_DIRECTORY_ENV, "relative/artifacts");
        assertThatThrownBy(() -> LegacyContainerItContract.activation(relative, queueProperties()))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("absolute");

        Map<String, String> missing = queueEnvironment();
        missing.put(
                LegacyContainerItContract.LEDGER_DIRECTORY_ENV,
                ledgerDirectory.resolve("missing").toString()
        );
        assertThatThrownBy(() -> LegacyContainerItContract.activation(missing, queueProperties()))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("existing");

        Path regularFile = Files.createFile(ledgerDirectory.resolve("not-a-directory"));
        Map<String, String> nonDirectory = queueEnvironment();
        nonDirectory.put(LegacyContainerItContract.LEDGER_DIRECTORY_ENV, regularFile.toString());
        assertThatThrownBy(() -> LegacyContainerItContract.activation(
                nonDirectory, queueProperties()
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("directory");

        Path real = Files.createDirectories(ledgerDirectory.resolve("real/nested"));
        Path alias = ledgerDirectory.resolve("alias");
        Files.createSymbolicLink(alias, real.getParent());
        Map<String, String> symlinked = queueEnvironment();
        symlinked.put(
                LegacyContainerItContract.LEDGER_DIRECTORY_ENV,
                alias.resolve("nested").toString()
        );
        assertThatThrownBy(() -> LegacyContainerItContract.activation(
                symlinked, queueProperties()
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("symlink");

        Path untrusted = Files.createDirectory(ledgerDirectory.resolve("world-writable"));
        Files.setPosixFilePermissions(untrusted, PosixFilePermissions.fromString("rwxrwxrwx"));
        Map<String, String> unsafePermissions = queueEnvironment();
        unsafePermissions.put(
                LegacyContainerItContract.LEDGER_DIRECTORY_ENV,
                untrusted.toString()
        );
        assertThatThrownBy(() -> LegacyContainerItContract.activation(
                unsafePermissions, queueProperties()
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("writable");

        Path notPrivate = Files.createDirectory(ledgerDirectory.resolve("not-private"));
        Files.setPosixFilePermissions(notPrivate, PosixFilePermissions.fromString("rwxr-xr-x"));
        Map<String, String> publicRead = queueEnvironment();
        publicRead.put(
                LegacyContainerItContract.LEDGER_DIRECTORY_ENV,
                notPrivate.toString()
        );
        assertThatThrownBy(() -> LegacyContainerItContract.activation(
                publicRead, queueProperties()
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("0700");

        Path unsafeAncestor = Files.createDirectory(ledgerDirectory.resolve("unsafe-ancestor"));
        Files.setPosixFilePermissions(
                unsafeAncestor,
                PosixFilePermissions.fromString("rwxrwxrwx")
        );
        Path privateChild = Files.createDirectory(unsafeAncestor.resolve("private-child"));
        Files.setPosixFilePermissions(
                privateChild,
                PosixFilePermissions.fromString("rwx------")
        );
        Map<String, String> unsafeAncestorEnvironment = queueEnvironment();
        unsafeAncestorEnvironment.put(
                LegacyContainerItContract.LEDGER_DIRECTORY_ENV,
                privateChild.toString()
        );
        assertThatThrownBy(() -> LegacyContainerItContract.activation(
                unsafeAncestorEnvironment, queueProperties()
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("writable ancestor");
    }

    @Test
    void endpointDiagnosticsNeverExposeThePostgresPassword() {
        LegacyContainerEndpoints.PostgresEndpoint endpoint =
                new LegacyContainerEndpoints.PostgresEndpoint(
                        "127.0.0.1",
                        15432,
                        "p007_acceptance",
                        "offline_fixture",
                        "offline_fixture_only"
                );

        assertThat(endpoint.toString())
                .doesNotContain("offline_fixture_only")
                .contains("password=<redacted>");

        LegacyContainerItContract.Activation activation =
                LegacyContainerItContract.Activation.forTesting(
                        "p007_redaction_01234567",
                        LegacyContainerItContract.Lane.PIPELINE,
                        ledgerDirectory,
                        endpoint,
                        null
                );
        assertThat(activation.toString())
                .doesNotContain("offline_fixture_only")
                .contains("postgres=<redacted>");
    }

    @Test
    void rejectsNonCanonicalPortsCredentialsAndGuardedProperties() {
        Map<String, String> leadingZeroPort = postgresEnvironment(
                LegacyContainerItContract.Lane.PIPELINE
        );
        leadingZeroPort.put(LegacyContainerItContract.POSTGRES_PORT_ENV, "015432");
        assertThatThrownBy(() -> LegacyContainerItContract.activation(
                leadingZeroPort, propertiesFor(LegacyContainerItContract.Lane.PIPELINE)
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("15432");

        for (Map.Entry<String, String> changedCredential : Map.of(
                LegacyContainerItContract.POSTGRES_DATABASE_ENV, "another_database",
                LegacyContainerItContract.POSTGRES_USERNAME_ENV, "another_user",
                LegacyContainerItContract.POSTGRES_PASSWORD_ENV, "another_password"
        ).entrySet()) {
            Map<String, String> environment = postgresEnvironment(
                    LegacyContainerItContract.Lane.PIPELINE
            );
            environment.put(changedCredential.getKey(), changedCredential.getValue());
            assertThatThrownBy(() -> LegacyContainerItContract.activation(
                    environment,
                    propertiesFor(LegacyContainerItContract.Lane.PIPELINE)
            )).isInstanceOf(IllegalStateException.class).hasMessageContaining("reviewed");
        }

        Map<String, String> unknownProperty = new HashMap<>(queueProperties());
        unknownProperty.put("codecrow.legacy-it.unreviewed", "present");
        assertThatThrownBy(() -> LegacyContainerItContract.activation(
                queueEnvironment(), unknownProperty
        )).isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("unknown guarded system property");
    }

    @Test
    void postgresEndpointTypeItselfRejectsEveryUnreviewedFixtureField() {
        assertThatThrownBy(() -> new LegacyContainerEndpoints.PostgresEndpoint(
                "127.0.0.1", 15432, "other", "offline_fixture", "offline_fixture_only"
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("database");
        assertThatThrownBy(() -> new LegacyContainerEndpoints.PostgresEndpoint(
                "127.0.0.1", 15432, "p007_acceptance", "other", "offline_fixture_only"
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("username");
        assertThatThrownBy(() -> new LegacyContainerEndpoints.PostgresEndpoint(
                "127.0.0.1", 15432, "p007_acceptance", "offline_fixture", "other"
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("password");
    }

    @Test
    void moduleVisibilityRequiresAllAndOnlyTheTargetLaneSelectors() {
        for (LegacyContainerItContract.Lane lane : LegacyContainerItContract.Lane.values()) {
            LegacyContainerItContract.Activation activation = lane ==
                    LegacyContainerItContract.Lane.QUEUE
                    ? LegacyContainerItContract.Activation.forTesting(
                            "p007_module_01234567",
                            lane,
                            ledgerDirectory,
                            null,
                            new LegacyContainerEndpoints.RedisEndpoint("127.0.0.1", 16379)
                    )
                    : LegacyContainerItContract.Activation.forTesting(
                            "p007_module_01234567",
                            lane,
                            ledgerDirectory,
                            new LegacyContainerEndpoints.PostgresEndpoint(
                                    "127.0.0.1",
                                    15432,
                                    "p007_acceptance",
                                    "offline_fixture",
                                    "offline_fixture_only"
                            ),
                            null
                    );
            Set<String> visible = new HashSet<>(Set.of(lane.selectors().split(",")));
            assertThatCode(() -> LegacyContainerModuleVisibility.assertExact(
                    activation,
                    visible::contains
            )).doesNotThrowAnyException();

            String requiredSelector = lane.selectors().split(",")[0];
            visible.remove(requiredSelector);
            assertThatThrownBy(() -> LegacyContainerModuleVisibility.assertExact(
                    activation,
                    visible::contains
            )).isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("target selector");

            visible.add(requiredSelector);
            visible.add(LegacyContainerItContract.Lane.values()[
                    (lane.ordinal() + 1) % LegacyContainerItContract.Lane.values().length
            ].selectors().split(",")[0]);
            assertThatThrownBy(() -> LegacyContainerModuleVisibility.assertExact(
                    activation,
                    visible::contains
            )).isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("non-target selector");
        }
    }

    @Test
    void failsClosedForPartialUnknownOrCrossLaneEnvironment() {
        assertThatThrownBy(() -> LegacyContainerItContract.activation(
                Map.of(
                        LegacyContainerItContract.PROTOCOL_ENV, "1",
                        LegacyContainerItContract.EXECUTOR_ENV, "maven-failsafe"
                ),
                Map.of()
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("RUN_ID");

        Map<String, String> unknown = queueEnvironment();
        unknown.put("CODECROW_LEGACY_IT_UNREVIEWED", "present");
        assertThatThrownBy(() -> LegacyContainerItContract.activation(unknown, queueProperties()))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("unknown guarded environment");

        Map<String, String> crossLane = queueEnvironment();
        crossLane.put(LegacyContainerItContract.POSTGRES_HOST_ENV, "127.0.0.1");
        assertThatThrownBy(() -> LegacyContainerItContract.activation(
                crossLane, queueProperties()
        )).isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("queue lane");
    }

    @Test
    void rejectsUnsupportedProtocolRunIdLaneAndExistingLedger() throws Exception {
        Map<String, String> protocol = queueEnvironment();
        protocol.put(LegacyContainerItContract.PROTOCOL_ENV, "2");
        assertThatThrownBy(() -> LegacyContainerItContract.activation(
                protocol, queueProperties()
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("protocol");

        Map<String, String> executor = queueEnvironment();
        executor.put(LegacyContainerItContract.EXECUTOR_ENV, "maven-surefire");
        assertThatThrownBy(() -> LegacyContainerItContract.activation(
                executor, queueProperties()
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("Failsafe");

        Map<String, String> runId = queueEnvironment();
        runId.put(LegacyContainerItContract.RUN_ID_ENV, "../unsafe");
        assertThatThrownBy(() -> LegacyContainerItContract.activation(runId, queueProperties()))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("run id");

        Map<String, String> lane = queueEnvironment();
        lane.put(LegacyContainerItContract.LANE_ENV, "unreviewed");
        assertThatThrownBy(() -> LegacyContainerItContract.activation(lane, queueProperties()))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("lane");

        Path existing = ledgerDirectory.resolve(
                "legacy-container-it-queue-p007_queue_01234567.json"
        );
        Files.createFile(existing);
        assertThatThrownBy(() -> LegacyContainerItContract.activation(
                queueEnvironment(), queueProperties()
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("already exists");
    }

    @Test
    void queueEndpointRequiresCanonicalLiteralHostAndPort() {
        Map<String, String> host = queueEnvironment();
        host.put(LegacyContainerItContract.REDIS_HOST_ENV, "localhost");
        assertThatThrownBy(() -> LegacyContainerItContract.activation(host, queueProperties()))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("127.0.0.1");

        Map<String, String> port = queueEnvironment();
        port.put(LegacyContainerItContract.REDIS_PORT_ENV, "016379");
        assertThatThrownBy(() -> LegacyContainerItContract.activation(port, queueProperties()))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("16379");
    }

    @Test
    void failsClosedForWrongSelectorModuleHostPortOrUnsafeCredentialText() {
        Map<String, String> environment = postgresEnvironment(
                LegacyContainerItContract.Lane.PIPELINE
        );
        Map<String, String> properties = propertiesFor(
                LegacyContainerItContract.Lane.PIPELINE
        );

        Map<String, String> wrongSelector = new HashMap<>(properties);
        wrongSelector.put("it.test", "org.example.WrongIT");
        assertThatThrownBy(() -> LegacyContainerItContract.activation(
                environment, wrongSelector
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("selector");

        Map<String, String> wrongModule = new HashMap<>(properties);
        wrongModule.put(LegacyContainerItContract.TARGET_ARTIFACT_PROPERTY, "codecrow-web-server");
        assertThatThrownBy(() -> LegacyContainerItContract.activation(
                environment, wrongModule
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("target artifact");

        Map<String, String> wrongHost = new HashMap<>(environment);
        wrongHost.put(LegacyContainerItContract.POSTGRES_HOST_ENV, "localhost");
        assertThatThrownBy(() -> LegacyContainerItContract.activation(wrongHost, properties))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("127.0.0.1");

        Map<String, String> wrongPort = new HashMap<>(environment);
        wrongPort.put(LegacyContainerItContract.POSTGRES_PORT_ENV, "5432");
        assertThatThrownBy(() -> LegacyContainerItContract.activation(wrongPort, properties))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("15432");

        Map<String, String> unsafePassword = new HashMap<>(environment);
        unsafePassword.put(LegacyContainerItContract.POSTGRES_PASSWORD_ENV, "line1\nline2");
        assertThatThrownBy(() -> LegacyContainerItContract.activation(
                unsafePassword, properties
        )).isInstanceOf(IllegalStateException.class)
                .hasMessageContaining(LegacyContainerItContract.POSTGRES_PASSWORD_ENV)
                .hasMessageNotContaining("line1");
    }

    @Test
    void activeContractRejectsDockerAndTestcontainersVisibility() {
        Map<String, String> docker = queueEnvironment();
        docker.put("DOCKER_HOST", "unix:///run/docker.sock");
        assertThatThrownBy(() -> LegacyContainerItContract.activation(docker, queueProperties()))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("DOCKER_HOST");

        Map<String, String> testcontainers = queueEnvironment();
        testcontainers.put("TESTCONTAINERS_RYUK_DISABLED", "true");
        assertThatThrownBy(() -> LegacyContainerItContract.activation(
                testcontainers, queueProperties()
        )).isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("TESTCONTAINERS_RYUK_DISABLED");
    }

    @Test
    void provisioningReceiptIsExactPrivateAndDigestBound() throws Exception {
        Map<String, String> wrongDigest = queueEnvironment();
        wrongDigest.put(LegacyContainerItContract.PROVISIONING_RECEIPT_SHA256_ENV, "0".repeat(64));
        assertThatThrownBy(() -> LegacyContainerItContract.activation(
                wrongDigest, queueProperties()
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("digest mismatch");

        Map<String, String> wrongContent = queueEnvironment();
        Path receipt = Path.of(wrongContent.get(
                LegacyContainerItContract.PROVISIONING_RECEIPT_ENV
        ));
        Files.setPosixFilePermissions(receipt, PosixFilePermissions.fromString("rw-------"));
        Files.writeString(
                receipt,
                Files.readString(receipt).replace("servicePort=16379", "servicePort=16378")
        );
        Files.setPosixFilePermissions(receipt, PosixFilePermissions.fromString("r--------"));
        wrongContent.put(
                LegacyContainerItContract.PROVISIONING_RECEIPT_SHA256_ENV,
                sha256(Files.readAllBytes(receipt))
        );
        assertThatThrownBy(() -> LegacyContainerItContract.activation(
                wrongContent, queueProperties()
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("contract mismatch");

        Map<String, String> publicMode = queueEnvironment();
        Path publicReceipt = Path.of(publicMode.get(
                LegacyContainerItContract.PROVISIONING_RECEIPT_ENV
        ));
        Files.setPosixFilePermissions(
                publicReceipt,
                PosixFilePermissions.fromString("r--r--r--")
        );
        assertThatThrownBy(() -> LegacyContainerItContract.activation(
                publicMode, queueProperties()
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("0400");
    }

    @Test
    void provisioningReceiptRejectsNonCanonicalLineEncoding() throws Exception {
        Map<String, String> missingFinalLf = queueEnvironment();
        Path receipt = Path.of(missingFinalLf.get(
                LegacyContainerItContract.PROVISIONING_RECEIPT_ENV
        ));
        rewriteReceipt(receipt, Files.readString(receipt).stripTrailing());
        missingFinalLf.put(
                LegacyContainerItContract.PROVISIONING_RECEIPT_SHA256_ENV,
                sha256(Files.readAllBytes(receipt))
        );
        assertThatThrownBy(() -> LegacyContainerItContract.activation(
                missingFinalLf, queueProperties()
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("canonical LF");

        Map<String, String> carriageReturn = queueEnvironment();
        Path carriageReturnReceipt = Path.of(carriageReturn.get(
                LegacyContainerItContract.PROVISIONING_RECEIPT_ENV
        ));
        rewriteReceipt(
                carriageReturnReceipt,
                Files.readString(carriageReturnReceipt).replace("\n", "\r\n")
        );
        carriageReturn.put(
                LegacyContainerItContract.PROVISIONING_RECEIPT_SHA256_ENV,
                sha256(Files.readAllBytes(carriageReturnReceipt))
        );
        assertThatThrownBy(() -> LegacyContainerItContract.activation(
                carriageReturn, queueProperties()
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("canonical ASCII");
    }

    @Test
    void endpointFacadesExposeEveryReviewedValueAndRejectWrongFixedPorts() {
        LegacyContainerEndpoints.PostgresEndpoint postgres =
                new LegacyContainerEndpoints.PostgresEndpoint(
                        "127.0.0.1",
                        15432,
                        "p007_acceptance",
                        "offline_fixture",
                        "offline_fixture_only"
                );
        assertThat(postgres.database()).isEqualTo("p007_acceptance");
        assertThat(postgres.username()).isEqualTo("offline_fixture");

        LegacyContainerEndpoints.RedisEndpoint redis =
                new LegacyContainerEndpoints.RedisEndpoint("127.0.0.1", 16379);
        assertThat(redis.springProperties()).containsExactly(
                "spring.redis.host=127.0.0.1",
                "spring.redis.port=16379"
        );
        assertThatThrownBy(() -> new LegacyContainerEndpoints.RedisEndpoint(
                "127.0.0.1", 6379
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("fixed guarded port");
        assertThatThrownBy(() -> new LegacyContainerEndpoints.PostgresEndpoint(
                "127.0.0.1",
                5432,
                "p007_acceptance",
                "offline_fixture",
                "offline_fixture_only"
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("fixed guarded port");
    }

    @Test
    void activationRejectsEnvironmentArtifactBlankAndInvalidPathShapes() {
        Map<String, String> wrongArtifact = queueEnvironment();
        wrongArtifact.put(LegacyContainerItContract.TARGET_ARTIFACT_ENV, "codecrow-web-server");
        assertThatThrownBy(() -> LegacyContainerItContract.activation(
                wrongArtifact, queueProperties()
        )).isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("environment target artifact");

        Map<String, String> blank = queueEnvironment();
        blank.put(LegacyContainerItContract.RUN_ID_ENV, "   ");
        assertThatThrownBy(() -> LegacyContainerItContract.activation(blank, queueProperties()))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("required contract value");

        Map<String, String> invalidLedger = queueEnvironment();
        invalidLedger.put(LegacyContainerItContract.LEDGER_DIRECTORY_ENV, "bad\0path");
        assertThatThrownBy(() -> LegacyContainerItContract.activation(
                invalidLedger, queueProperties()
        )).isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("invalid external-call ledger directory");

        Map<String, String> invalidReceipt = queueEnvironment();
        invalidReceipt.put(LegacyContainerItContract.PROVISIONING_RECEIPT_ENV, "bad\0path");
        assertThatThrownBy(() -> LegacyContainerItContract.activation(
                invalidReceipt, queueProperties()
        )).isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("invalid guarded provisioning receipt path");
    }

    @Test
    void receiptMustBeTheExactRegularPrivateLaneArtifact() throws Exception {
        Map<String, String> wrongPath = queueEnvironment();
        Path original = Path.of(wrongPath.get(
                LegacyContainerItContract.PROVISIONING_RECEIPT_ENV
        ));
        Path alternate = ledgerDirectory.resolve("alternate.receipt");
        Files.copy(original, alternate);
        Files.setPosixFilePermissions(alternate, PosixFilePermissions.fromString("r--------"));
        wrongPath.put(LegacyContainerItContract.PROVISIONING_RECEIPT_ENV, alternate.toString());
        wrongPath.put(
                LegacyContainerItContract.PROVISIONING_RECEIPT_SHA256_ENV,
                sha256(Files.readAllBytes(alternate))
        );
        assertThatThrownBy(() -> LegacyContainerItContract.activation(
                wrongPath, queueProperties()
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("exact regular");

        Map<String, String> symlinked = queueEnvironment();
        Path receipt = Path.of(symlinked.get(
                LegacyContainerItContract.PROVISIONING_RECEIPT_ENV
        ));
        Path target = ledgerDirectory.resolve("receipt-target");
        Files.move(receipt, target);
        Files.createSymbolicLink(receipt, target);
        assertThatThrownBy(() -> LegacyContainerItContract.activation(
                symlinked, queueProperties()
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("exact regular");

        Map<String, String> directoryReceipt = queueEnvironment();
        Path nonRegular = Path.of(directoryReceipt.get(
                LegacyContainerItContract.PROVISIONING_RECEIPT_ENV
        ));
        Files.delete(nonRegular);
        Files.createDirectory(nonRegular);
        assertThatThrownBy(() -> LegacyContainerItContract.activation(
                directoryReceipt, queueProperties()
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("exact regular");
    }

    @Test
    void receiptRejectsOwnerSizeAsciiAndDigestSyntaxFailures() throws Exception {
        Map<String, String> wrongOwner = queueEnvironment();
        Path receipt = Path.of(wrongOwner.get(
                LegacyContainerItContract.PROVISIONING_RECEIPT_ENV
        ));
        try (MockedStatic<Files> files = mockStatic(Files.class, CALLS_REAL_METHODS)) {
            files.when(() -> Files.getOwner(receipt, LinkOption.NOFOLLOW_LINKS))
                    .thenReturn(mock(UserPrincipal.class));
            assertThatThrownBy(() -> LegacyContainerItContract.activation(
                    wrongOwner, queueProperties()
            )).isInstanceOf(IllegalStateException.class).hasMessageContaining("owned");
        }

        Map<String, String> empty = queueEnvironment();
        Path emptyReceipt = Path.of(empty.get(
                LegacyContainerItContract.PROVISIONING_RECEIPT_ENV
        ));
        rewriteReceipt(emptyReceipt, "");
        empty.put(
                LegacyContainerItContract.PROVISIONING_RECEIPT_SHA256_ENV,
                sha256(Files.readAllBytes(emptyReceipt))
        );
        assertThatThrownBy(() -> LegacyContainerItContract.activation(empty, queueProperties()))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("size");

        Map<String, String> oversized = queueEnvironment();
        Path oversizedReceipt = Path.of(oversized.get(
                LegacyContainerItContract.PROVISIONING_RECEIPT_ENV
        ));
        rewriteReceipt(oversizedReceipt, "x".repeat(4096) + "\n");
        oversized.put(
                LegacyContainerItContract.PROVISIONING_RECEIPT_SHA256_ENV,
                sha256(Files.readAllBytes(oversizedReceipt))
        );
        assertThatThrownBy(() -> LegacyContainerItContract.activation(
                oversized, queueProperties()
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("size");

        Map<String, String> highAscii = queueEnvironment();
        Path highAsciiReceipt = Path.of(highAscii.get(
                LegacyContainerItContract.PROVISIONING_RECEIPT_ENV
        ));
        Files.setPosixFilePermissions(
                highAsciiReceipt, PosixFilePermissions.fromString("rw-------")
        );
        byte[] invalid = Files.readAllBytes(highAsciiReceipt);
        invalid[0] = (byte) 0xff;
        Files.write(highAsciiReceipt, invalid);
        Files.setPosixFilePermissions(
                highAsciiReceipt, PosixFilePermissions.fromString("r--------")
        );
        highAscii.put(
                LegacyContainerItContract.PROVISIONING_RECEIPT_SHA256_ENV,
                sha256(invalid)
        );
        assertThatThrownBy(() -> LegacyContainerItContract.activation(
                highAscii, queueProperties()
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("canonical ASCII");

        Map<String, String> invalidDigest = queueEnvironment();
        invalidDigest.put(
                LegacyContainerItContract.PROVISIONING_RECEIPT_SHA256_ENV,
                "not-a-sha256"
        );
        assertThatThrownBy(() -> LegacyContainerItContract.activation(
                invalidDigest, queueProperties()
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("digest mismatch");
    }

    @Test
    void everyReceiptFieldAndFieldCountIsIndependentlyValidated() throws Exception {
        for (int field = 0; field < 11; field++) {
            Map<String, String> environment = queueEnvironment();
            Path receipt = Path.of(environment.get(
                    LegacyContainerItContract.PROVISIONING_RECEIPT_ENV
            ));
            List<String> lines = new ArrayList<>(Files.readAllLines(receipt));
            lines.set(field, "invalid-field-" + field);
            rewriteReceipt(receipt, String.join("\n", lines) + "\n");
            environment.put(
                    LegacyContainerItContract.PROVISIONING_RECEIPT_SHA256_ENV,
                    sha256(Files.readAllBytes(receipt))
            );
            assertThatThrownBy(() -> LegacyContainerItContract.activation(
                    environment, queueProperties()
            )).isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("contract mismatch");
        }

        Map<String, String> environment = queueEnvironment();
        Path receipt = Path.of(environment.get(
                LegacyContainerItContract.PROVISIONING_RECEIPT_ENV
        ));
        List<String> tooFew = new ArrayList<>(Files.readAllLines(receipt));
        tooFew.remove(tooFew.size() - 1);
        rewriteReceipt(receipt, String.join("\n", tooFew) + "\n");
        environment.put(
                LegacyContainerItContract.PROVISIONING_RECEIPT_SHA256_ENV,
                sha256(Files.readAllBytes(receipt))
        );
        assertThatThrownBy(() -> LegacyContainerItContract.activation(
                environment, queueProperties()
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("contract mismatch");
    }

    @Test
    void receiptFilesystemAndDigestProviderFailuresAreFailClosed() throws Exception {
        Map<String, String> unsupported = queueEnvironment();
        Path receipt = Path.of(unsupported.get(
                LegacyContainerItContract.PROVISIONING_RECEIPT_ENV
        ));
        try (MockedStatic<Files> files = mockStatic(Files.class, CALLS_REAL_METHODS)) {
            files.when(() -> Files.getPosixFilePermissions(
                    receipt, LinkOption.NOFOLLOW_LINKS
            )).thenThrow(new UnsupportedOperationException("no posix"));
            assertThatThrownBy(() -> LegacyContainerItContract.activation(
                    unsupported, queueProperties()
            )).isInstanceOf(IllegalStateException.class).hasMessageContaining("POSIX nofollow");
        }

        Map<String, String> unreadable = queueEnvironment();
        Path unreadableReceipt = Path.of(unreadable.get(
                LegacyContainerItContract.PROVISIONING_RECEIPT_ENV
        ));
        try (MockedStatic<Files> files = mockStatic(Files.class, CALLS_REAL_METHODS)) {
            files.when(() -> Files.readAllBytes(unreadableReceipt))
                    .thenThrow(new IOException("cannot read"));
            assertThatThrownBy(() -> LegacyContainerItContract.activation(
                    unreadable, queueProperties()
            )).isInstanceOf(IllegalStateException.class).hasMessageContaining("cannot verify");
        }

        try (MockedStatic<MessageDigest> digests = mockStatic(MessageDigest.class)) {
            digests.when(() -> MessageDigest.getInstance("SHA-256"))
                    .thenThrow(new NoSuchAlgorithmException("missing"));
            assertThatThrownBy(() -> invokeContractSha256(new byte[]{1}))
                    .isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("SHA-256 is unavailable");
        }
    }

    @Test
    void activationConstructorRejectsEveryImpossibleServiceCombination() {
        LegacyContainerEndpoints.PostgresEndpoint postgres =
                new LegacyContainerEndpoints.PostgresEndpoint(
                        "127.0.0.1",
                        15432,
                        "p007_acceptance",
                        "offline_fixture",
                        "offline_fixture_only"
                );
        LegacyContainerEndpoints.RedisEndpoint redis =
                new LegacyContainerEndpoints.RedisEndpoint("127.0.0.1", 16379);

        assertThatThrownBy(() -> LegacyContainerItContract.Activation.forTesting(
                "p007_invalid_01234567",
                LegacyContainerItContract.Lane.QUEUE,
                ledgerDirectory,
                null,
                null
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("exactly one");
        assertThatThrownBy(() -> LegacyContainerItContract.Activation.forTesting(
                "p007_invalid_01234567",
                LegacyContainerItContract.Lane.WEB,
                ledgerDirectory,
                postgres,
                redis
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("exactly one");
        assertThatThrownBy(() -> LegacyContainerItContract.Activation.forTesting(
                "p007_invalid_01234567",
                LegacyContainerItContract.Lane.QUEUE,
                ledgerDirectory,
                postgres,
                null
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("requires Redis");
        assertThatThrownBy(() -> LegacyContainerItContract.Activation.forTesting(
                "p007_invalid_01234567",
                LegacyContainerItContract.Lane.WEB,
                ledgerDirectory,
                null,
                redis
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("requires PostgreSQL");

        LegacyContainerItContract.Activation queue =
                LegacyContainerItContract.Activation.forTesting(
                        "p007_tostring_01234567",
                        LegacyContainerItContract.Lane.QUEUE,
                        ledgerDirectory,
                        null,
                        redis
                );
        assertThat(queue.toString()).contains("postgres=absent", "redis=RedisEndpoint");
    }

    @Test
    void moduleVisibilityUsesFallbackLoaderAndWrapsLinkageErrors() throws Throwable {
        Thread thread = Thread.currentThread();
        ClassLoader original = thread.getContextClassLoader();
        String visible = LegacyContainerModuleVisibility.class.getName();
        try {
            thread.setContextClassLoader(null);
            assertThat(invokeModuleVisibility(visible)).isEqualTo(true);

            thread.setContextClassLoader(new ClassLoader(original) {
                @Override
                protected Class<?> loadClass(String name, boolean resolve)
                        throws ClassNotFoundException {
                    if (name.equals(visible)) {
                        throw new NoClassDefFoundError("broken selector");
                    }
                    return super.loadClass(name, resolve);
                }
            });
            assertThatThrownBy(() -> invokeModuleVisibility(visible))
                    .isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("cannot be linked")
                    .hasCauseInstanceOf(NoClassDefFoundError.class);
        } finally {
            thread.setContextClassLoader(original);
        }
    }

    private static Object invokeContractSha256(byte[] content) throws Throwable {
        java.lang.reflect.Method method = LegacyContainerItContract.class.getDeclaredMethod(
                "sha256", byte[].class
        );
        method.setAccessible(true);
        try {
            return method.invoke(null, (Object) content);
        } catch (java.lang.reflect.InvocationTargetException failure) {
            throw failure.getCause();
        }
    }

    private static Object invokeModuleVisibility(String className) throws Throwable {
        java.lang.reflect.Method method = LegacyContainerModuleVisibility.class.getDeclaredMethod(
                "isVisible", String.class
        );
        method.setAccessible(true);
        try {
            return method.invoke(null, className);
        } catch (java.lang.reflect.InvocationTargetException failure) {
            throw failure.getCause();
        }
    }

    private static void rewriteReceipt(Path receipt, String content) throws IOException {
        Files.setPosixFilePermissions(receipt, PosixFilePermissions.fromString("rw-------"));
        Files.writeString(receipt, content, StandardCharsets.UTF_8);
        Files.setPosixFilePermissions(receipt, PosixFilePermissions.fromString("r--------"));
    }

    private Map<String, String> queueEnvironment() {
        Map<String, String> environment = commonEnvironment(
                LegacyContainerItContract.Lane.QUEUE,
                "p007_queue_01234567"
        );
        environment.put(LegacyContainerItContract.REDIS_HOST_ENV, "127.0.0.1");
        environment.put(LegacyContainerItContract.REDIS_PORT_ENV, "16379");
        return environment;
    }

    private Map<String, String> postgresEnvironment(LegacyContainerItContract.Lane lane) {
        Map<String, String> environment = commonEnvironment(lane, "p007_pg_01234567");
        environment.put(LegacyContainerItContract.POSTGRES_HOST_ENV, "127.0.0.1");
        environment.put(LegacyContainerItContract.POSTGRES_PORT_ENV, "15432");
        environment.put(LegacyContainerItContract.POSTGRES_DATABASE_ENV, "p007_acceptance");
        environment.put(LegacyContainerItContract.POSTGRES_USERNAME_ENV, "offline_fixture");
        environment.put(LegacyContainerItContract.POSTGRES_PASSWORD_ENV, "offline_fixture_only");
        return environment;
    }

    private Map<String, String> commonEnvironment(
            LegacyContainerItContract.Lane lane,
            String runId
    ) {
        Map<String, String> environment = new HashMap<>();
        environment.put(LegacyContainerItContract.PROTOCOL_ENV, "1");
        environment.put(LegacyContainerItContract.EXECUTOR_ENV, "maven-failsafe");
        environment.put(LegacyContainerItContract.RUN_ID_ENV, runId);
        environment.put(LegacyContainerItContract.LANE_ENV, lane.id());
        environment.put(LegacyContainerItContract.TARGET_ARTIFACT_ENV, lane.targetArtifact());
        environment.put(
                LegacyContainerItContract.LEDGER_DIRECTORY_ENV,
                ledgerDirectory.toAbsolutePath().toString()
        );
        Path receipt = writeReceipt(lane, runId);
        environment.put(
                LegacyContainerItContract.PROVISIONING_RECEIPT_ENV,
                receipt.toString()
        );
        try {
            environment.put(
                    LegacyContainerItContract.PROVISIONING_RECEIPT_SHA256_ENV,
                    sha256(Files.readAllBytes(receipt))
            );
        } catch (IOException failure) {
            throw new UncheckedIOException(failure);
        }
        return environment;
    }

    private Path writeReceipt(LegacyContainerItContract.Lane lane, String runId) {
        Path receipt = ledgerDirectory.resolve("provisioning.receipt");
        String image = lane == LegacyContainerItContract.Lane.QUEUE
                ? "redis@sha256:6ab0b6e7381779332f97b8ca76193e45b0756f38d4c0dcda72dbb3c32061ab99"
                : "postgres@sha256:e013e867e712fec275706a6c51c966f0bb0c93cfa8f51000f85a15f9865a28cb";
        int port = lane == LegacyContainerItContract.Lane.QUEUE ? 16379 : 15432;
        try {
            Files.deleteIfExists(receipt);
            Files.writeString(
                    receipt,
                    String.join("\n",
                            "schemaVersion=1",
                            "runId=" + runId,
                            "lane=" + lane.id(),
                            "targetArtifact=" + lane.targetArtifact(),
                            "namespace=codecrow-" + runId.replace('_', '-') + "-" + lane.id(),
                            "policySha256="
                                    + "a3c2e03ee6b88f6f88619741de0968048e33848f4b5f7eaa04cb29001f420d23",
                            "imageManifestSha256="
                                    + "a0c1f1063fadb33cc486760abeeb0edd2a1889c790ac69e9a1a12529cf3ae71c",
                            "imageReference=" + image,
                            "containerId=" + "c".repeat(64),
                            "serviceHost=127.0.0.1",
                            "servicePort=" + port
                    ) + "\n",
                    StandardCharsets.UTF_8
            );
            Files.setPosixFilePermissions(
                    receipt,
                    PosixFilePermissions.fromString("r--------")
            );
            return receipt;
        } catch (IOException failure) {
            throw new UncheckedIOException(failure);
        }
    }

    private static String sha256(byte[] content) {
        try {
            return HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256").digest(content)
            );
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException(impossible);
        }
    }

    private Map<String, String> queueProperties() {
        return propertiesFor(LegacyContainerItContract.Lane.QUEUE);
    }

    private Map<String, String> propertiesFor(LegacyContainerItContract.Lane lane) {
        return Map.of(
                LegacyContainerItContract.TARGET_ARTIFACT_PROPERTY, lane.targetArtifact(),
                "it.test", lane.selectors()
        );
    }
}
