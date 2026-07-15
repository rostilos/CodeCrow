package org.rostilos.codecrow.taskmanagement.offline.p003;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.RegisterExtension;
import org.rostilos.codecrow.taskmanagement.jira.cloud.JiraCloudClient;
import org.rostilos.codecrow.taskmanagement.jira.cloud.JiraCloudConfig;
import org.rostilos.codecrow.taskmanagement.model.TaskDetails;
import org.rostilos.codecrow.testsupport.offline.ExternalCall;
import org.rostilos.codecrow.testsupport.offline.ExternalCallLedger;
import org.rostilos.codecrow.testsupport.offline.NetworkDenyGuard;
import org.rostilos.codecrow.testsupport.offline.OfflineNetworkExtension;
import org.rostilos.codecrow.testsupport.offline.ScriptedScenario;
import org.rostilos.codecrow.testsupport.offline.ScriptedStep;
import org.rostilos.codecrow.testsupport.offline.UnexpectedExternalCall;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.FutureTask;
import java.util.concurrent.TimeUnit;
import java.util.function.Supplier;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class JiraCloudAdapterContractTest {

    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();

    @RegisterExtension
    final OfflineNetworkExtension offlineNetwork = new OfflineNetworkExtension();

    @Test
    void productionAdapterAndScriptedFakeShareTheNeutralTaskContract() throws Exception {
        JsonNode fixture = sharedJiraFixture();
        JsonNode responseBody = fixture.path("routes").get(0).path("response").path("body");
        ExternalCallLedger ledger = offlineNetwork.ledger();
        ScriptedScenario script = new ScriptedScenario(
                "jira-neutral-task-v1",
                "jira",
                "fake-jira:18080",
                List.of(
                        ScriptedStep.structuredResponse("get_task", 1, responseBody.toString()),
                        ScriptedStep.structuredResponse("get_task", 2, responseBody.toString())
                ),
                ledger
        );
        RecordedHttpRequest request;
        try (LiteralHttpFixture fixtureServer = new LiteralHttpFixture(
                () -> script.next("get_task").step().payload().asText()
        )) {
            String baseUrl = "http://127.0.0.1:" + fixtureServer.port();
            TaskLookup fake = taskId -> taskFromFixture(
                    script.next("get_task").step().payload().asText(),
                    baseUrl
            );

            try (NetworkDenyGuard.NetworkLease ignored =
                         offlineNetwork.allowLoopback("127.0.0.1", fixtureServer.port())) {
                TaskLookup production = new JiraCloudClient(new JiraCloudConfig(
                        baseUrl,
                        "offline@example.invalid",
                        "offline-fixture-token"
                ))::getTaskDetails;
                assertTaskContract(production);
            }
            assertTaskContract(fake);
            request = fixtureServer.awaitRequest();
        }

        assertThat(request.method()).isEqualTo("GET");
        assertThat(request.path())
                .startsWith("/rest/api/3/issue/NEUTRAL-7?fields=")
                .contains("summary", "description", "status");
        assertThat(request.header("Authorization")).startsWith("Basic ");
        assertThat(fixture.path("provider").asText()).isEqualTo("jira");
        assertThat(fixture.path("schema_version").asText()).isEqualTo("1.0");
        assertThat(script.remaining()).isZero();
        assertThat(ledger.entries()).satisfiesExactly(
                JiraCloudAdapterContractTest::assertSimulatedJiraCall,
                JiraCloudAdapterContractTest::assertSimulatedJiraCall
        );
        assertThat(ledger.entries().toString()).doesNotContain("offline-fixture-token");
    }

    @Test
    void literalFixturePropagatesHandlerFaultAndClosesItsListener() throws Exception {
        LiteralHttpFixture fixture = new LiteralHttpFixture(() -> {
            throw new IllegalStateException("scripted fixture fault");
        });

        try (fixture;
             NetworkDenyGuard.NetworkLease ignored =
                     offlineNetwork.allowLoopback("127.0.0.1", fixture.port());
             Socket client = new Socket("127.0.0.1", fixture.port())) {
            client.getOutputStream().write((
                    "GET /fault HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n"
            ).getBytes(StandardCharsets.US_ASCII));
            client.getOutputStream().flush();

            assertThatThrownBy(fixture::awaitRequest)
                    .isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("scripted fixture fault");
        }

        assertThat(fixture.isClosed()).isTrue();
        assertThat(offlineNetwork.ledger().entries()).isEmpty();
    }

    @Test
    void unregisteredProductionJiraTargetIsDeniedBeforeDns() {
        JiraCloudClient production = new JiraCloudClient(new JiraCloudConfig(
                "https://jira-provider.invalid",
                "offline@example.invalid",
                "offline-fixture-token"
        ));

        assertThatThrownBy(production::validateConnection)
                .isInstanceOf(UnexpectedExternalCall.class);
        ExternalCall blocked = offlineNetwork.ledger().entries().get(0);
        assertThat(offlineNetwork.ledger().entries()).containsExactly(blocked);
        assertThat(blocked).satisfies(call -> {
            assertThat(call.boundary()).isEqualTo("network");
            assertThat(call.live()).isFalse();
            assertThat(call.operation()).isEqualTo("resolve");
            assertThat(call.outcome()).isEqualTo("blocked");
            assertThat(call.phase()).isEqualTo("PRE_DNS");
            assertThat(call.simulated()).isFalse();
            assertThat(call.target()).isEqualTo("jira-provider.invalid:0");
        });
        offlineNetwork.acknowledgeBlockedCall(
                blocked, "network", "resolve", "PRE_DNS", "jira-provider.invalid:0"
        );
    }

    private static void assertTaskContract(TaskLookup lookup) throws IOException {
        TaskDetails task = lookup.get("NEUTRAL-7");
        assertThat(task.taskId()).isEqualTo("NEUTRAL-7");
        assertThat(task.summary()).isEqualTo("Neutral fixture task");
        assertThat(task.webUrl()).endsWith("/browse/NEUTRAL-7");
    }

    private static void assertSimulatedJiraCall(ExternalCall call) {
        assertThat(call.boundary()).isEqualTo("jira");
        assertThat(call.live()).isFalse();
        assertThat(call.outcome()).isEqualTo("structured");
        assertThat(call.phase()).isEqualTo("SIMULATED");
        assertThat(call.simulated()).isTrue();
    }

    private static TaskDetails taskFromFixture(String fixtureJson, String baseUrl) throws IOException {
        JsonNode body = OBJECT_MAPPER.readTree(fixtureJson);
        String taskId = body.path("key").asText();
        return new TaskDetails(
                taskId,
                body.path("fields").path("summary").asText(),
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                baseUrl + "/browse/" + taskId
        );
    }

    private static JsonNode sharedJiraFixture() throws IOException {
        Path current = Path.of("").toAbsolutePath();
        while (current != null) {
            Path candidate = current.resolve(
                    "tools/offline-harness/fixtures/protocol/jira-v1.json"
            );
            if (Files.isRegularFile(candidate)) {
                return OBJECT_MAPPER.readTree(candidate.toFile());
            }
            current = current.getParent();
        }
        throw new IllegalStateException("shared Jira fixture not found");
    }

    @FunctionalInterface
    private interface TaskLookup {
        TaskDetails get(String taskId) throws IOException;
    }

    private record RecordedHttpRequest(String method, String path, Map<String, String> headers) {
        private String header(String name) {
            return headers.get(name.toLowerCase(Locale.ROOT));
        }
    }

    /** One-request HTTP fixture that never asks the JVM for a host name. */
    private static final class LiteralHttpFixture implements AutoCloseable {

        private final ServerSocket listener;
        private final FutureTask<RecordedHttpRequest> exchange;

        private LiteralHttpFixture(Supplier<String> responseBody) throws IOException {
            listener = new ServerSocket();
            listener.bind(new InetSocketAddress(
                    InetAddress.getByAddress(new byte[]{127, 0, 0, 1}),
                    0
            ));
            listener.setSoTimeout(5_000);
            exchange = new FutureTask<>(() -> serve(responseBody));
            Thread serverThread = new Thread(exchange, "jira-literal-http-fixture");
            serverThread.setDaemon(true);
            serverThread.start();
        }

        private int port() {
            return listener.getLocalPort();
        }

        private RecordedHttpRequest awaitRequest() throws Exception {
            try {
                return exchange.get(5, TimeUnit.SECONDS);
            } catch (ExecutionException failure) {
                if (failure.getCause() instanceof Exception exception) {
                    throw exception;
                }
                throw new AssertionError("literal HTTP fixture failed", failure.getCause());
            }
        }

        private RecordedHttpRequest serve(Supplier<String> responseBody) throws Exception {
            try (Socket client = listener.accept()) {
                BufferedReader reader = new BufferedReader(new InputStreamReader(
                        client.getInputStream(), StandardCharsets.US_ASCII
                ));
                String requestLine = reader.readLine();
                if (requestLine == null) {
                    throw new IOException("fixture received no HTTP request line");
                }
                String[] requestParts = requestLine.split(" ", 3);
                if (requestParts.length != 3) {
                    throw new IOException("fixture received a malformed HTTP request line");
                }
                Map<String, String> headers = new LinkedHashMap<>();
                for (String line = reader.readLine(); line != null && !line.isEmpty(); line = reader.readLine()) {
                    int separator = line.indexOf(':');
                    if (separator < 1) {
                        throw new IOException("fixture received a malformed HTTP header");
                    }
                    headers.put(
                            line.substring(0, separator).toLowerCase(Locale.ROOT),
                            line.substring(separator + 1).trim()
                    );
                }

                byte[] body = responseBody.get().getBytes(StandardCharsets.UTF_8);
                byte[] responseHead = (
                        "HTTP/1.1 200 OK\r\n"
                                + "Content-Type: application/json\r\n"
                                + "Content-Length: " + body.length + "\r\n"
                                + "Connection: close\r\n\r\n"
                ).getBytes(StandardCharsets.US_ASCII);
                OutputStream output = client.getOutputStream();
                output.write(responseHead);
                output.write(body);
                output.flush();
                return new RecordedHttpRequest(requestParts[0], requestParts[1], Map.copyOf(headers));
            }
        }

        private boolean isClosed() {
            return listener.isClosed();
        }

        @Override
        public void close() throws IOException {
            listener.close();
        }
    }
}
