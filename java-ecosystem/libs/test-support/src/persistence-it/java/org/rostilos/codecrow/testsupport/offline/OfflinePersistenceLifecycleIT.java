package org.rostilos.codecrow.testsupport.offline;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.github.dockerjava.api.model.Container;
import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.RequestBody;
import okhttp3.Response;
import org.junit.jupiter.api.Test;
import org.testcontainers.DockerClientFactory;
import org.testcontainers.lifecycle.Startables;
import org.testcontainers.utility.DockerImageName;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.EOFException;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.atomic.AtomicReference;
import java.util.regex.Pattern;
import java.util.stream.Stream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assertions.assertThrows;

/**
 * Explicit-profile acceptance test for the real local persistence lifecycle.
 * The profile is intentionally absent from the default test source set.
 */
class OfflinePersistenceLifecycleIT {

    private static final String LOOPBACK_HOST = "127.0.0.1";
    private static final String CONTAINER_ID_REPORT = "CODECROW_PERSISTENCE_CONTAINER_IDS";
    private static final String PERSISTENCE_LEDGER = "CODECROW_EXTERNAL_CALL_LEDGER";
    private static final Pattern FULL_CONTAINER_ID = Pattern.compile("^[0-9a-f]{64}$");
    private static final ObjectMapper REPORT_MAPPER = new ObjectMapper();

    @Test
    void resetsRealStoresRestartsCleanAndLeavesNoOwnedContainersPresent() throws Exception {
        assertThat(System.getenv("TESTCONTAINERS_RYUK_DISABLED")).isEqualTo("true");
        assertThat(System.getenv("TESTCONTAINERS_HOST_OVERRIDE")).isEqualTo(LOOPBACK_HOST);
        OfflinePersistenceSupport.Namespace namespace = OfflinePersistenceSupport.namespace(
                "offline-persistence-lifecycle-v1"
        );
        List<OwnedContainer> ownedContainers = new ArrayList<>();
        ExternalCallLedger persistenceLedger = new ExternalCallLedger();

        OfflinePersistenceSupport.runAndCleanup(
                () -> {
                    exerciseGeneration(
                            namespace,
                            "first-generation",
                            true,
                            ownedContainers,
                            persistenceLedger
                    );
                    exerciseGeneration(
                            namespace,
                            "restarted-generation",
                            false,
                            ownedContainers,
                            persistenceLedger
                    );
                    assertThat(ownedContainers)
                            .extracting(container -> container.generation() + "/" + container.service())
                            .containsExactly(
                                    "first-generation/postgres",
                                    "first-generation/redis",
                                    "first-generation/qdrant",
                                    "restarted-generation/postgres",
                                    "restarted-generation/redis",
                                    "restarted-generation/qdrant"
                            );
                    assertThat(ownedContainers)
                            .extracting(OwnedContainer::containerId)
                            .doesNotHaveDuplicates()
                            .allSatisfy(containerId ->
                                    assertThat(containerId).matches(FULL_CONTAINER_ID)
                            );
                },
                () -> OfflinePersistenceSupport.runAndCleanup(
                        () -> finalizeOwnedContainerEvidence(namespace, ownedContainers),
                        () -> finalizePersistenceLedger(persistenceLedger)
                )
        );
    }

    private static void exerciseGeneration(
            OfflinePersistenceSupport.Namespace namespace,
            String marker,
            boolean proveUnregisteredTargetDenied,
            List<OwnedContainer> ownedContainers,
            ExternalCallLedger persistenceLedger
    ) throws Exception {
        OfflinePersistenceSupport.Containers containers = OfflinePersistenceSupport.containers(namespace);
        assertPinnedLocalOnlyNonReusable(containers);

        try (containers) {
            try {
                Startables.deepStart(Stream.of(
                        containers.postgres(),
                        containers.redis(),
                        containers.qdrant()
                )).join();
            } finally {
                rememberContainerId(
                        marker, "postgres", containers.postgres().getContainerId(), ownedContainers
                );
                rememberContainerId(
                        marker, "redis", containers.redis().getContainerId(), ownedContainers
                );
                rememberContainerId(
                        marker, "qdrant", containers.qdrant().getContainerId(), ownedContainers
                );
            }

            assertThat(containers.postgres().getDockerImageName())
                    .isEqualTo(OfflinePersistenceSupport.POSTGRES_IMAGE);
            assertThat(containers.redis().getDockerImageName())
                    .isEqualTo(OfflinePersistenceSupport.REDIS_IMAGE);
            assertThat(containers.qdrant().getDockerImageName())
                    .isEqualTo(OfflinePersistenceSupport.QDRANT_IMAGE);

            assertThat(containers.postgres().getHost()).isEqualTo(LOOPBACK_HOST);
            assertThat(containers.redis().getHost()).isEqualTo(LOOPBACK_HOST);
            assertThat(containers.qdrant().getHost()).isEqualTo(LOOPBACK_HOST);
            int postgresPort = containers.postgres().getMappedPort(5432);
            int redisPort = containers.redis().getMappedPort(6379);
            int qdrantPort = containers.qdrant().getMappedPort(6333);
            String postgresDatabase = containers.postgres().getDatabaseName();
            String postgresUsername = containers.postgres().getUsername();
            String postgresPassword = containers.postgres().getPassword();

            try (OfflineNetworkBoundary boundary = OfflineNetworkBoundary.install(persistenceLedger);
                 NetworkDenyGuard.NetworkLease postgresLease =
                         boundary.allowLoopback(LOOPBACK_HOST, postgresPort);
                 NetworkDenyGuard.NetworkLease redisLease =
                         boundary.allowLoopback(LOOPBACK_HOST, redisPort);
                 NetworkDenyGuard.NetworkLease qdrantLease =
                         boundary.allowLoopback(LOOPBACK_HOST, qdrantPort)) {
                if (proveUnregisteredTargetDenied) {
                    assertUnregisteredLiteralTargetDenied(persistenceLedger);
                }
                try (PersistenceClients clients = PersistenceClients.connect(
                        postgresDatabase,
                        postgresUsername,
                        postgresPassword,
                        postgresPort,
                        redisPort,
                        qdrantPort
                )) {
                    clients.assertClean(namespace);
                    clients.writeAndRead(namespace, marker);
                    OfflinePersistenceSupport.reset(
                            namespace,
                            clients::resetPostgres,
                            clients::resetRedis,
                            clients::resetQdrant
                    );
                    clients.assertClean(namespace);
                }
            }
        }

        assertThat(containers.postgres().isRunning()).isFalse();
        assertThat(containers.redis().isRunning()).isFalse();
        assertThat(containers.qdrant().isRunning()).isFalse();
        assertOwnedContainersAbsent(inspectOwnedContainerStatuses(ownedContainers));
    }

    private static void assertUnregisteredLiteralTargetDenied(
        ExternalCallLedger persistenceLedger
    ) {
        int priorEntries = persistenceLedger.entries().size();
        UnexpectedExternalCall denial = assertThrows(
                UnexpectedExternalCall.class,
                () -> {
                    InetAddress unregisteredAddress = InetAddress.getByAddress(
                            new byte[]{(byte) 203, 0, 113, 7}
                    );
                    try (Socket socket = new Socket()) {
                        socket.connect(new InetSocketAddress(unregisteredAddress, 9), 250);
                    }
                }
        );

        assertThat(persistenceLedger.entries()).hasSize(priorEntries + 1);
        ExternalCall blocked = denial.call();
        assertThat(persistenceLedger.entries().get(priorEntries)).isSameAs(blocked);
        assertThat(blocked).satisfies(call -> {
            assertThat(call.boundary()).isEqualTo("network");
            assertThat(call.live()).isFalse();
            assertThat(call.operation()).isEqualTo("connect");
            assertThat(call.outcome()).isEqualTo("blocked");
            assertThat(call.phase()).isEqualTo("PRE_SOCKET");
            assertThat(call.sequence()).isEqualTo(priorEntries + 1L);
            assertThat(call.simulated()).isFalse();
            assertThat(call.target()).isEqualTo("203.0.113.7:9");
        });
        persistenceLedger.acknowledgeBlocked(
                blocked,
                "network",
                "connect",
                "PRE_SOCKET",
                "203.0.113.7:9"
        );
    }

    private static void finalizePersistenceLedger(
            ExternalCallLedger persistenceLedger
    ) throws Exception {
        OfflinePersistenceSupport.runAndCleanup(
                () -> OfflinePersistenceSupport.runAndCleanup(
                        persistenceLedger::assertZeroLiveCalls,
                        persistenceLedger::assertNoUnacknowledgedBlockedCalls
                ),
                () -> OfflinePersistenceSupport.runAndCleanup(
                        () -> assertThat(persistenceLedger.entries()).singleElement().satisfies(call -> {
                            assertThat(call.boundary()).isEqualTo("network");
                            assertThat(call.operation()).isEqualTo("connect");
                            assertThat(call.outcome()).isEqualTo("blocked");
                            assertThat(call.phase()).isEqualTo("PRE_SOCKET");
                            assertThat(call.live()).isFalse();
                            assertThat(call.simulated()).isFalse();
                            assertThat(call.target()).isEqualTo("203.0.113.7:9");
                        }),
                        () -> persistenceLedger.writeJson(persistenceLedgerPath())
                )
        );
    }

    private static Path persistenceLedgerPath() {
        String configuredDestination = System.getenv(PERSISTENCE_LEDGER);
        if (configuredDestination == null || configuredDestination.isBlank()) {
            throw new IllegalStateException(
                    PERSISTENCE_LEDGER + " must name the canonical persistence ledger"
            );
        }
        return Path.of(configuredDestination).toAbsolutePath().normalize();
    }

    private static void assertPinnedLocalOnlyNonReusable(
            OfflinePersistenceSupport.Containers containers
    ) {
        assertThat(List.of(
                OfflinePersistenceSupport.POSTGRES_IMAGE,
                OfflinePersistenceSupport.REDIS_IMAGE,
                OfflinePersistenceSupport.QDRANT_IMAGE
        )).allSatisfy(reference -> {
            assertThat(reference).contains("@sha256:").doesNotContain(":latest");
            assertThat(OfflinePersistenceSupport.LOCAL_ONLY_PULL_POLICY.shouldPull(
                    DockerImageName.parse(reference)
            )).isFalse();
        });
        assertThat(containers.postgres().isShouldBeReused()).isFalse();
        assertThat(containers.redis().isShouldBeReused()).isFalse();
        assertThat(containers.qdrant().isShouldBeReused()).isFalse();
    }

    private static void rememberContainerId(
            String generation,
            String service,
            String containerId,
            List<OwnedContainer> ownedContainers
    ) {
        if (containerId != null
                && !containerId.isBlank()
                && ownedContainers.stream().noneMatch(owned -> owned.containerId().equals(containerId))) {
            ownedContainers.add(new OwnedContainer(generation, service, containerId));
        }
    }

    private static List<OwnedContainerStatus> inspectOwnedContainerStatuses(
            List<OwnedContainer> ownedContainers
    ) {
        if (ownedContainers.isEmpty()) {
            return List.of();
        }
        Set<String> ownedContainerIds = ownedContainers.stream()
                .map(OwnedContainer::containerId)
                .collect(java.util.stream.Collectors.toCollection(LinkedHashSet::new));
        Map<String, Container> retainedOwnedContainers = DockerClientFactory.instance().client()
                .listContainersCmd()
                .withShowAll(true)
                .withIdFilter(ownedContainerIds)
                .exec()
                .stream()
                .filter(container -> ownedContainerIds.contains(container.getId()))
                .collect(java.util.stream.Collectors.toMap(
                        Container::getId,
                        container -> container
                ));
        return ownedContainers.stream()
                .map(owned -> {
                    Container retained = retainedOwnedContainers.get(owned.containerId());
                    String status = retained == null
                            ? "absent"
                            : "present:" + normalizedContainerState(retained.getState());
                    return new OwnedContainerStatus(
                            owned.generation(), owned.service(), owned.containerId(), status
                    );
                })
                .toList();
    }

    private static String normalizedContainerState(String state) {
        if (state == null || state.isBlank()) {
            return "unknown";
        }
        return state.strip().toLowerCase(Locale.ROOT);
    }

    private static void assertOwnedContainersAbsent(List<OwnedContainerStatus> statuses) {
        assertThat(statuses).allSatisfy(status ->
                assertThat(status.status())
                        .as("container %s (%s/%s)",
                                status.containerId(), status.generation(), status.service())
                        .isEqualTo("absent")
        );
    }

    private static void finalizeOwnedContainerEvidence(
            OfflinePersistenceSupport.Namespace namespace,
            List<OwnedContainer> ownedContainers
    ) throws Exception {
        AtomicReference<List<OwnedContainerStatus>> statuses = new AtomicReference<>(
                ownedContainers.stream()
                        .map(owned -> new OwnedContainerStatus(
                                owned.generation(),
                                owned.service(),
                                owned.containerId(),
                                "inspection-not-completed"
                        ))
                        .toList()
        );
        OfflinePersistenceSupport.runAndCleanup(
                () -> statuses.set(inspectOwnedContainerStatuses(ownedContainers)),
                () -> OfflinePersistenceSupport.runAndCleanup(
                        () -> writeContainerIdReport(namespace, statuses.get()),
                        () -> assertOwnedContainersAbsent(statuses.get())
                )
        );
    }

    private static void writeContainerIdReport(
            OfflinePersistenceSupport.Namespace namespace,
            List<OwnedContainerStatus> statuses
    ) throws IOException {
        String configuredDestination = System.getenv(CONTAINER_ID_REPORT);
        if (configuredDestination == null || configuredDestination.isBlank()) {
            return;
        }
        Path destination = Path.of(configuredDestination).toAbsolutePath().normalize();
        Path parent = destination.getParent();
        Files.createDirectories(parent);
        Path temporary = Files.createTempFile(
                parent,
                "." + destination.getFileName() + "-",
                ".tmp"
        );
        try {
            REPORT_MAPPER.writerWithDefaultPrettyPrinter().writeValue(
                    temporary.toFile(),
                    new ContainerIdReport(
                            "1.0",
                            namespace.postgresSchema(),
                            List.copyOf(statuses)
                    )
            );
            try {
                Files.move(
                        temporary,
                        destination,
                        StandardCopyOption.ATOMIC_MOVE,
                        StandardCopyOption.REPLACE_EXISTING
                );
            } catch (AtomicMoveNotSupportedException unsupported) {
                Files.move(
                        temporary,
                        destination,
                        StandardCopyOption.REPLACE_EXISTING
                );
            }
        } finally {
            Files.deleteIfExists(temporary);
        }
    }

    private record OwnedContainer(String generation, String service, String containerId) {
    }

    private record OwnedContainerStatus(
            String generation,
            String service,
            String containerId,
            String status
    ) {
    }

    private record ContainerIdReport(
            String schemaVersion,
            String scenarioNamespace,
            List<OwnedContainerStatus> containers
    ) {
    }

    private static final class PersistenceClients implements AutoCloseable {

        private static final MediaType JSON = MediaType.get("application/json");
        private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();
        private final Connection postgres;
        private final RedisRespClient redis;
        private final String qdrantBaseUrl;
        private final OkHttpClient http;

        private PersistenceClients(
                Connection postgres,
                RedisRespClient redis,
                String qdrantBaseUrl,
                OkHttpClient http
        ) {
            this.postgres = postgres;
            this.redis = redis;
            this.qdrantBaseUrl = qdrantBaseUrl;
            this.http = http;
        }

        private static PersistenceClients connect(
                String postgresDatabase,
                String postgresUsername,
                String postgresPassword,
                int postgresPort,
                int redisPort,
                int qdrantPort
        ) throws Exception {
            Connection postgres = DriverManager.getConnection(
                    "jdbc:postgresql://" + LOOPBACK_HOST + ":" + postgresPort
                            + "/" + postgresDatabase,
                    postgresUsername,
                    postgresPassword
            );
            RedisRespClient redis = null;
            try {
                redis = RedisRespClient.connect(redisPort);
                String qdrantBaseUrl = "http://" + LOOPBACK_HOST + ":" + qdrantPort;
                OkHttpClient http = new OkHttpClient.Builder()
                        .callTimeout(Duration.ofSeconds(15))
                        .build();
                return new PersistenceClients(postgres, redis, qdrantBaseUrl, http);
            } catch (Throwable failure) {
                if (redis != null) {
                    try {
                        redis.close();
                    } catch (Throwable cleanupFailure) {
                        failure.addSuppressed(cleanupFailure);
                    }
                }
                try {
                    postgres.close();
                } catch (Throwable cleanupFailure) {
                    failure.addSuppressed(cleanupFailure);
                }
                if (failure instanceof Exception exception) {
                    throw exception;
                }
                throw (Error) failure;
            }
        }

        private void assertClean(OfflinePersistenceSupport.Namespace namespace) throws Exception {
            assertThat(postgresTableExists(namespace)).isFalse();
            assertThat(redis.command("GET", redisKey(namespace))).isNull();
            assertThat(qdrantGet("/collections/" + namespace.qdrantCollection()).status())
                    .isEqualTo(404);
        }

        private void writeAndRead(
                OfflinePersistenceSupport.Namespace namespace,
                String marker
        ) throws Exception {
            writePostgres(namespace, marker);
            assertThat(readPostgres(namespace)).isEqualTo(marker);

            assertThat(redis.command("SET", redisKey(namespace), marker)).isEqualTo("OK");
            assertThat(redis.command("GET", redisKey(namespace))).isEqualTo(marker);

            HttpResult collectionCreated = qdrantPut(
                    "/collections/" + namespace.qdrantCollection(),
                    "{\"vectors\":{\"size\":4,\"distance\":\"Cosine\"}}"
            );
            assertStatus(collectionCreated, 200);
            HttpResult pointWritten = qdrantPut(
                    "/collections/" + namespace.qdrantCollection() + "/points?wait=true",
                    "{\"points\":[{\"id\":1,\"vector\":[0.1,0.2,0.3,0.4],"
                            + "\"payload\":{\"marker\":\"" + marker + "\"}}]}"
            );
            assertStatus(pointWritten, 200);
            HttpResult pointRead = qdrantGet(
                    "/collections/" + namespace.qdrantCollection() + "/points/1"
            );
            assertStatus(pointRead, 200);
            JsonNode markerNode = OBJECT_MAPPER.readTree(pointRead.body()).findValue("marker");
            assertThat(markerNode).isNotNull();
            assertThat(markerNode.asText()).isEqualTo(marker);
        }

        private void writePostgres(
                OfflinePersistenceSupport.Namespace namespace,
                String marker
        ) throws SQLException {
            String schema = quoted(namespace.postgresSchema());
            try (Statement statement = postgres.createStatement()) {
                statement.execute("CREATE SCHEMA IF NOT EXISTS " + schema);
                statement.execute("CREATE TABLE " + schema
                        + ".fixture_values (id INTEGER PRIMARY KEY, marker TEXT NOT NULL)");
            }
            try (PreparedStatement insert = postgres.prepareStatement(
                    "INSERT INTO " + schema + ".fixture_values (id, marker) VALUES (1, ?)"
            )) {
                insert.setString(1, marker);
                assertThat(insert.executeUpdate()).isEqualTo(1);
            }
        }

        private String readPostgres(OfflinePersistenceSupport.Namespace namespace) throws SQLException {
            String schema = quoted(namespace.postgresSchema());
            try (Statement statement = postgres.createStatement();
                 ResultSet result = statement.executeQuery(
                         "SELECT marker FROM " + schema + ".fixture_values WHERE id = 1"
                 )) {
                assertThat(result.next()).isTrue();
                String marker = result.getString(1);
                assertThat(result.next()).isFalse();
                return marker;
            }
        }

        private boolean postgresTableExists(
                OfflinePersistenceSupport.Namespace namespace
        ) throws SQLException {
            try (PreparedStatement query = postgres.prepareStatement("SELECT to_regclass(?)")) {
                query.setString(1, namespace.postgresSchema() + ".fixture_values");
                try (ResultSet result = query.executeQuery()) {
                    assertThat(result.next()).isTrue();
                    return result.getString(1) != null;
                }
            }
        }

        private void resetPostgres(String schema) throws SQLException {
            String quotedSchema = quoted(schema);
            try (Statement statement = postgres.createStatement()) {
                statement.execute("DROP SCHEMA IF EXISTS " + quotedSchema + " CASCADE");
                statement.execute("CREATE SCHEMA " + quotedSchema);
            }
        }

        private void resetRedis(String prefix) throws IOException {
            Object keysReply = redis.command("KEYS", prefix + "*");
            if (!(keysReply instanceof List<?> values)) {
                throw new IOException("Redis KEYS returned a non-array response");
            }
            List<String> keys = values.stream()
                    .map(value -> (String) value)
                    .toList();
            if (keys.isEmpty()) {
                return;
            }
            List<String> delete = new ArrayList<>();
            delete.add("DEL");
            delete.addAll(keys);
            assertThat(redis.command(delete.toArray(String[]::new)))
                    .isEqualTo((long) keys.size());
        }

        private void resetQdrant(String collection) throws IOException {
            assertStatus(qdrantDelete("/collections/" + collection), 200);
        }

        private HttpResult qdrantGet(String path) throws IOException {
            return qdrantExchange(new Request.Builder().url(qdrantBaseUrl + path).get().build());
        }

        private HttpResult qdrantPut(String path, String json) throws IOException {
            Request request = new Request.Builder()
                    .url(qdrantBaseUrl + path)
                    .put(RequestBody.create(json, JSON))
                    .build();
            return qdrantExchange(request);
        }

        private HttpResult qdrantDelete(String path) throws IOException {
            return qdrantExchange(new Request.Builder().url(qdrantBaseUrl + path).delete().build());
        }

        private HttpResult qdrantExchange(Request request) throws IOException {
            try (Response response = http.newCall(request).execute()) {
                String body = response.body() == null ? "" : response.body().string();
                return new HttpResult(response.code(), body);
            }
        }

        private static void assertStatus(HttpResult result, int expected) {
            assertThat(result.status())
                    .withFailMessage("Qdrant returned %s: %s", result.status(), result.body())
                    .isEqualTo(expected);
        }

        private static String redisKey(OfflinePersistenceSupport.Namespace namespace) {
            return namespace.redisPrefix() + "fixture-value";
        }

        private static String quoted(String identifier) {
            return "\"" + identifier.replace("\"", "\"\"") + "\"";
        }

        @Override
        public void close() throws Exception {
            OfflinePersistenceSupport.runAndCleanup(
                    redis::close,
                    () -> OfflinePersistenceSupport.runAndCleanup(
                            postgres::close,
                            () -> {
                                http.connectionPool().evictAll();
                                http.dispatcher().executorService().shutdownNow();
                            }
                    )
            );
        }
    }

    private static final class RedisRespClient implements AutoCloseable {

        private static final int MAX_RESPONSE_LINE_BYTES = 1_048_576;
        private final Socket socket;
        private final InputStream input;
        private final OutputStream output;

        private RedisRespClient(Socket socket) throws IOException {
            this.socket = socket;
            this.input = new BufferedInputStream(socket.getInputStream());
            this.output = new BufferedOutputStream(socket.getOutputStream());
        }

        private static RedisRespClient connect(int port) throws IOException {
            Socket socket = new Socket();
            boolean connected = false;
            try {
                socket.connect(new InetSocketAddress(
                        InetAddress.getByAddress(new byte[]{127, 0, 0, 1}),
                        port
                ), 5_000);
                socket.setSoTimeout(5_000);
                RedisRespClient client = new RedisRespClient(socket);
                connected = true;
                return client;
            } finally {
                if (!connected) {
                    socket.close();
                }
            }
        }

        private synchronized Object command(String... arguments) throws IOException {
            writeAscii("*" + arguments.length + "\r\n");
            for (String argument : arguments) {
                byte[] bytes = argument.getBytes(StandardCharsets.UTF_8);
                writeAscii("$" + bytes.length + "\r\n");
                output.write(bytes);
                writeAscii("\r\n");
            }
            output.flush();
            return readReply();
        }

        private void writeAscii(String value) throws IOException {
            output.write(value.getBytes(StandardCharsets.US_ASCII));
        }

        private Object readReply() throws IOException {
            int type = input.read();
            if (type < 0) {
                throw new EOFException("Redis closed the connection before replying");
            }
            return switch (type) {
                case '+' -> readResponseLine();
                case '-' -> throw new IOException("Redis error response: " + readResponseLine());
                case ':' -> parseLong(readResponseLine(), "integer");
                case '$' -> readBulkString();
                case '*' -> readArray();
                default -> throw new IOException("unsupported Redis response prefix: " + type);
            };
        }

        private String readBulkString() throws IOException {
            long length = parseLong(readResponseLine(), "bulk-string length");
            if (length == -1) {
                return null;
            }
            if (length < 0 || length > Integer.MAX_VALUE) {
                throw new IOException("invalid Redis bulk-string length: " + length);
            }
            byte[] value = input.readNBytes((int) length);
            if (value.length != (int) length) {
                throw new EOFException("Redis closed during a bulk-string response");
            }
            requireCrlf();
            return new String(value, StandardCharsets.UTF_8);
        }

        private List<Object> readArray() throws IOException {
            long length = parseLong(readResponseLine(), "array length");
            if (length < 0 || length > Integer.MAX_VALUE) {
                throw new IOException("invalid Redis array length: " + length);
            }
            List<Object> values = new ArrayList<>((int) length);
            for (int index = 0; index < length; index++) {
                values.add(readReply());
            }
            return List.copyOf(values);
        }

        private String readResponseLine() throws IOException {
            byte[] value = new byte[MAX_RESPONSE_LINE_BYTES];
            int length = 0;
            while (length < value.length) {
                int next = input.read();
                if (next < 0) {
                    throw new EOFException("Redis closed during a response line");
                }
                if (next == '\r') {
                    if (input.read() != '\n') {
                        throw new IOException("Redis response line has an invalid terminator");
                    }
                    return new String(value, 0, length, StandardCharsets.UTF_8);
                }
                value[length++] = (byte) next;
            }
            throw new IOException("Redis response line exceeds the offline fixture limit");
        }

        private void requireCrlf() throws IOException {
            if (input.read() != '\r' || input.read() != '\n') {
                throw new IOException("Redis bulk-string response has an invalid terminator");
            }
        }

        private static long parseLong(String value, String field) throws IOException {
            try {
                return Long.parseLong(value);
            } catch (NumberFormatException failure) {
                throw new IOException("invalid Redis " + field + ": " + value, failure);
            }
        }

        @Override
        public void close() throws IOException {
            socket.close();
        }
    }

    private record HttpResult(int status, String body) {
    }
}
