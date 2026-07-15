package org.rostilos.codecrow.testsupport.offline;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.stream.LongStream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class ExternalCallLedgerTest {

    @TempDir
    Path temporaryDirectory;

    @Test
    void appendsSequencedRedactedEntriesAndExportsCanonicalJson() throws Exception {
        ExternalCallLedger ledger = new ExternalCallLedger();

        ExternalCall first = ledger.record(
                "llm",
                false,
                "chat",
                "response",
                "SIMULATED",
                true,
                "https://fixture.invalid/v1?api_key=top-secret&token=also-secret"
        );
        ExternalCall second = ledger.record(
                "vcs",
                false,
                "fetch",
                "blocked",
                "PRE_DNS",
                false,
                "unregistered.invalid:443"
        );

        assertThat(first.sequence()).isEqualTo(1);
        assertThat(second.sequence()).isEqualTo(2);
        assertThat(first.target())
                .isEqualTo("https://fixture.invalid");
        assertThat(second.operation()).isEqualTo("fetch");
        assertThat(ledger.liveCallCount()).isZero();
        ledger.assertZeroLiveCalls();
        assertThat(ledger.entries()).containsExactly(first, second);
        assertThatThrownBy(() -> ledger.entries().clear())
                .isInstanceOf(UnsupportedOperationException.class);

        Path json = temporaryDirectory.resolve("nested/external-calls.json");
        ledger.writeJson(json);

        String contents = Files.readString(json);
        assertThat(contents)
                .doesNotContain("top-secret", "also-secret");
        JsonNode exported = new ObjectMapper().readTree(contents);
        assertThat(exported.get("schema_version").asText()).isEqualTo("1.0");
        assertThat(exported.get("live_call_count").asLong()).isZero();
        assertThat(exported.get("simulated_call_count").asLong()).isEqualTo(1);
        JsonNode firstCall = exported.get("calls").get(0);
        assertThat(firstCall.get("boundary").asText()).isEqualTo("llm");
        assertThat(firstCall.get("live").asBoolean()).isFalse();
        assertThat(firstCall.get("operation").asText()).isEqualTo("chat");
        assertThat(firstCall.get("outcome").asText()).isEqualTo("response");
        assertThat(firstCall.get("phase").asText()).isEqualTo("SIMULATED");
        assertThat(firstCall.get("sequence").asLong()).isEqualTo(1);
        assertThat(firstCall.get("simulated").asBoolean()).isTrue();
        assertThat(firstCall.get("target").asText()).isEqualTo("https://fixture.invalid");
    }

    @Test
    void concurrentAppendsAreStoredInExactSequenceOrder() throws Exception {
        ExternalCallLedger ledger = new ExternalCallLedger();
        int callCount = 128;
        ExecutorService executor = Executors.newFixedThreadPool(8);
        CountDownLatch start = new CountDownLatch(1);
        List<Future<ExternalCall>> calls = LongStream.range(0, callCount)
                .mapToObj(ignored -> executor.submit(() -> {
                    start.await();
                    return ledger.record(
                            "llm",
                            false,
                            "invoke",
                            "response",
                            "SIMULATED",
                            true,
                            "fake-llm:24117"
                    );
                }))
                .toList();

        try {
            start.countDown();
            for (Future<ExternalCall> call : calls) {
                call.get();
            }
        } finally {
            executor.shutdownNow();
        }

        assertThat(ledger.entries())
                .extracting(ExternalCall::sequence)
                .containsExactlyElementsOf(LongStream.rangeClosed(1, callCount).boxed().toList());
        assertThat(ledger.snapshot().simulatedCallCount()).isEqualTo(callCount);
    }

    @Test
    void fallsBackFromAtomicMoveAndLeavesNoTemporaryFile() throws Exception {
        Path destination = temporaryDirectory.resolve("external-calls.json");
        Files.writeString(destination, "obsolete");
        AtomicInteger moveAttempts = new AtomicInteger();
        ExternalCallLedger ledger = new ExternalCallLedger(
                new ObjectMapper(),
                (source, target, options) -> {
                    assertThat(source.getParent()).isEqualTo(target.getParent());
                    if (moveAttempts.getAndIncrement() == 0) {
                        throw new AtomicMoveNotSupportedException(
                                source.toString(), target.toString(), "scripted fallback"
                        );
                    }
                    Files.move(source, target, options);
                }
        );
        ledger.record("llm", false, "invoke", "response", "SIMULATED", true,
                "fake-llm:24117");

        ledger.writeJson(destination);

        assertThat(moveAttempts).hasValue(2);
        assertThat(new ObjectMapper().readValue(
                destination.toFile(), ExternalCallLedgerDocument.class
        )).isEqualTo(ledger.snapshot());
        try (var files = Files.list(temporaryDirectory)) {
            assertThat(files).containsExactly(destination);
        }
    }

    @Test
    void serializationFailureKeepsPreviousDocumentAndCleansPartialTemporaryFile() throws Exception {
        Path destination = temporaryDirectory.resolve("external-calls.json");
        String previousDocument = "{\"schema_version\":\"previous\"}";
        Files.writeString(destination, previousDocument);
        ObjectMapper failingMapper = new ObjectMapper() {
            @Override
            public byte[] writeValueAsBytes(Object value) throws JsonProcessingException {
                throw new JsonProcessingException("scripted serialization failure") { };
            }
        };
        ExternalCallLedger ledger = new ExternalCallLedger(failingMapper, Files::move);
        ledger.record("llm", false, "invoke", "response", "SIMULATED", true,
                "fake-llm:24117");

        assertThatThrownBy(() -> ledger.writeJson(destination))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("scripted serialization failure");

        assertThat(Files.readString(destination)).isEqualTo(previousDocument);
        try (var files = Files.list(temporaryDirectory)) {
            assertThat(files).containsExactly(destination);
        }
    }

    @Test
    void countsOnlyCallsThatActuallyReachedALiveBoundary() {
        ExternalCallLedger ledger = new ExternalCallLedger();

        ledger.record("email", true, "send", "sent", "PRE_SOCKET", false, "mail.example.invalid:443");
        ledger.record("email", false, "send", "blocked", "PRE_DNS", false, "mail.example.invalid:443");

        assertThat(ledger.liveCallCount()).isEqualTo(1);
        assertThat(ledger.simulatedCallCount()).isZero();
        assertThatThrownBy(ledger::assertZeroLiveCalls)
                .isInstanceOf(AssertionError.class)
                .hasMessageContaining("recorded 1");
    }

    @Test
    void normalizesStringsAndFailsClosedForTargetsAndPhases() {
        ExternalCallLedger ledger = new ExternalCallLedger();

        assertThat(ledger.record(
                "  VCS_GITHUB \n",
                false,
                "\tGET ",
                " RESPONSE  ",
                " simulated\t",
                true,
                "  HTTPS://user:password@Example.INVALID:8443/private?token=secret#prompt \n"
        )).satisfies(call -> {
            assertThat(call.boundary()).isEqualTo("vcs_github");
            assertThat(call.operation()).isEqualTo("get");
            assertThat(call.outcome()).isEqualTo("response");
            assertThat(call.phase()).isEqualTo("SIMULATED");
            assertThat(call.target()).isEqualTo("https://example.invalid:8443");
        });
        assertThat(ledger.record(
                "vcs",
                false,
                "get",
                "blocked",
                "PRE_DNS",
                false,
                "  SAFE.Example.INVALID:443\t"
        ).target()).isEqualTo("safe.example.invalid:443");
        assertThat(ledger.record(
                "vcs",
                false,
                "get",
                "blocked",
                "PRE_DNS",
                false,
                "  SAFE.Example.INVALID\t"
        ).target()).isEqualTo("safe.example.invalid:0");
        assertThat(ledger.record(
                "vcs",
                false,
                "get",
                "blocked",
                "PRE_DNS",
                false,
                "  FE80::1\t"
        ).target()).isEqualTo("[fe80::1]:0");
        assertThat(ledger.record(
                "vcs",
                false,
                "get",
                "blocked",
                "PRE_DNS",
                false,
                "  [FE80::2]\t"
        ).target()).isEqualTo("[fe80::2]:0");
        assertThat(ledger.record(
                "vcs",
                false,
                "get",
                "blocked",
                "PRE_EXEC",
                false,
                "plain-host.invalid"
        ).target()).isEqualTo("<redacted-target>");
        assertThat(ledger.record(
                "vcs",
                false,
                "get",
                "blocked",
                "PRE_SOCKET",
                false,
                "not a uri credential"
        ).target()).isEqualTo("<redacted-target>");
        assertThatThrownBy(() -> ledger.record(
                "vcs", false, "get", "blocked", "LIVE", false, "host.invalid:443"
        )).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> ledger.record(
                "bad.boundary", false, "get", "blocked", "PRE_DNS", false, "host.invalid:443"
        )).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> ledger.record(
                "vcs", false, "bad operation", "blocked", "PRE_DNS", false, "host.invalid:443"
        )).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> ledger.record(
                "vcs", false, "get", "bad.outcome", "PRE_DNS", false, "host.invalid:443"
        )).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> ledger.record(
                "vcs", true, "get", "response", "PRE_SOCKET", true, "host.invalid:443"
        )).isInstanceOf(IllegalArgumentException.class);

        List<Runnable> nullTextFields = List.of(
                () -> ledger.record(null, false, "get", "blocked", "PRE_DNS", false, "host.invalid:443"),
                () -> ledger.record("vcs", false, null, "blocked", "PRE_DNS", false, "host.invalid:443"),
                () -> ledger.record("vcs", false, "get", null, "PRE_DNS", false, "host.invalid:443"),
                () -> ledger.record("vcs", false, "get", "blocked", null, false, "host.invalid:443"),
                () -> ledger.record("vcs", false, "get", "blocked", "PRE_DNS", false, null)
        );
        assertThat(nullTextFields).allSatisfy(invocation -> assertThatThrownBy(invocation::run)
                .isInstanceOf(NullPointerException.class));

        List<Runnable> blankTextFields = List.of(
                () -> ledger.record(" \t", false, "get", "blocked", "PRE_DNS", false, "host.invalid:443"),
                () -> ledger.record("vcs", false, "\n", "blocked", "PRE_DNS", false, "host.invalid:443"),
                () -> ledger.record("vcs", false, "get", " ", "PRE_DNS", false, "host.invalid:443"),
                () -> ledger.record("vcs", false, "get", "blocked", "\t", false, "host.invalid:443"),
                () -> ledger.record("vcs", false, "get", "blocked", "PRE_DNS", false, " \n")
        );
        assertThat(blankTextFields).allSatisfy(invocation -> assertThatThrownBy(invocation::run)
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("must not be blank"));
    }

    @Test
    void acknowledgesOnlyTheExactOwnedBlockedCallAndReportsLateSequences() {
        ExternalCallLedger ledger = new ExternalCallLedger();
        ExternalCall blocked = ledger.record(
                "network", false, "connect", "blocked", "PRE_DNS", false,
                "api.openai.invalid:443"
        );

        assertThatThrownBy(ledger::assertNoUnacknowledgedBlockedCalls)
                .isInstanceOf(AssertionError.class)
                .hasMessageContaining("sequence(s): 1");
        assertThatThrownBy(() -> ledger.acknowledgeBlocked(
                blocked, "email", "connect", "PRE_DNS", "api.openai.invalid:443"
        )).isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("does not match");
        assertThatThrownBy(() -> ledger.acknowledgeBlocked(
                blocked, "network", "send", "PRE_DNS", "api.openai.invalid:443"
        )).isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("does not match");
        assertThatThrownBy(() -> ledger.acknowledgeBlocked(
                blocked, "network", "connect", "PRE_SOCKET", "api.openai.invalid:443"
        )).isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("does not match");
        assertThatThrownBy(() -> ledger.acknowledgeBlocked(
                blocked, "network", "connect", "PRE_DNS", "different.invalid:443"
        )).isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("does not match");

        ExternalCallLedger other = new ExternalCallLedger();
        ExternalCall forgedEqual = other.record(
                "network", false, "connect", "blocked", "PRE_DNS", false,
                "api.openai.invalid:443"
        );
        assertThat(forgedEqual).isEqualTo(blocked).isNotSameAs(blocked);
        assertThatThrownBy(() -> ledger.acknowledgeBlocked(
                forgedEqual, "network", "connect", "PRE_DNS", "api.openai.invalid:443"
        )).isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("recorded blocked");

        ExternalCall response = ledger.record(
                "network", false, "connect", "response", "SIMULATED", true,
                "api.openai.invalid:443"
        );
        assertThatThrownBy(() -> ledger.acknowledgeBlocked(
                response, "network", "connect", "SIMULATED", "api.openai.invalid:443"
        )).isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("recorded blocked");
        assertThatThrownBy(() -> ledger.acknowledgeBlocked(
                null, "network", "connect", "PRE_DNS", "api.openai.invalid:443"
        )).isInstanceOf(NullPointerException.class);

        ledger.acknowledgeBlocked(
                blocked, " NETWORK ", " CONNECT ", " pre_dns ",
                " API.OPENAI.INVALID:0443 "
        );
        ledger.acknowledgeBlocked(
                blocked, "network", "connect", "PRE_DNS", "api.openai.invalid:443"
        );
        ledger.assertNoUnacknowledgedBlockedCalls();

        ExternalCall late = ledger.record(
                "process", false, "exec", "blocked", "PRE_EXEC", false, "/usr/bin/curl"
        );
        assertThatThrownBy(ledger::assertNoUnacknowledgedBlockedCalls)
                .isInstanceOf(AssertionError.class)
                .hasMessageContaining("sequence(s): 3");
        ledger.acknowledgeBlocked(late, "process", "exec", "PRE_EXEC", "/usr/bin/curl");
        ledger.assertNoUnacknowledgedBlockedCalls();
    }

    @Test
    void consumesEverySharedTargetRedactionCaseExactly() throws Exception {
        Path goldenPath = SharedFixtureLocator.locate(
                "tools/offline-harness/fixtures/golden/target-redaction-v1.json"
        );
        JsonNode golden = new ObjectMapper().readTree(goldenPath.toFile());
        assertThat(golden.get("schema_version").asText()).isEqualTo("1.0");
        ExternalCallLedger ledger = new ExternalCallLedger();

        for (JsonNode fixture : golden.withArray("cases")) {
            String input = fixture.get("input").asText();
            ExternalCall call = ledger.record(
                    "network", false, "connect", "blocked", "PRE_DNS", false, input
            );
            assertThat(call.target()).isEqualTo(fixture.get("output").asText());
            ledger.acknowledgeBlocked(call, "network", "connect", "PRE_DNS", input);
        }

        String oversizedPort = "example.invalid:" + "9".repeat(20);
        ExternalCall oversized = ledger.record(
                "network", false, "connect", "blocked", "PRE_SOCKET", false, oversizedPort
        );
        assertThat(oversized.target()).isEqualTo("<redacted-target>");
        ledger.acknowledgeBlocked(
                oversized, "network", "connect", "PRE_SOCKET", oversizedPort
        );
        ExternalCall scopedIpv6 = ledger.record(
                "network", false, "connect", "blocked", "PRE_SOCKET", false,
                "http://[fe80::1%25eth0]:80/private"
        );
        assertThat(scopedIpv6.target()).isEqualTo("<redacted-target>");
        ledger.acknowledgeBlocked(
                scopedIpv6, "network", "connect", "PRE_SOCKET",
                "http://[fe80::1%25eth0]:80/private"
        );
        ledger.assertNoUnacknowledgedBlockedCalls();
    }

    @Test
    void consumesSharedGoldenAndWritesCanonicalSerializationSample() throws Exception {
        Path goldenPath = SharedFixtureLocator.locate(
                "tools/offline-harness/fixtures/golden/external-call-ledger-v1.json"
        );
        ExternalCallLedgerDocument golden = new ObjectMapper().readValue(
                goldenPath.toFile(),
                ExternalCallLedgerDocument.class
        );
        ExternalCallLedger ledger = new ExternalCallLedger();
        ledger.record("network", false, "connect", "blocked", "PRE_DNS", false,
                "api.openai.invalid:443");
        ledger.record("llm", false, "invoke", "structured", "SIMULATED", true,
                "fake-llm:24117");
        ledger.record("jira", false, "page", "page", "SIMULATED", true,
                "fake-jira:18080");

        assertThat(ledger.snapshot()).isEqualTo(golden);
        ledger.assertZeroLiveCalls();

        Path serializationSample = serializationSampleDestination();
        ledger.writeJson(serializationSample);
        assertThat(new ObjectMapper().readValue(
                serializationSample.toFile(), ExternalCallLedgerDocument.class
        )).isEqualTo(golden);
    }

    private static Path serializationSampleDestination() {
        String configured = System.getenv("CODECROW_EXTERNAL_CALL_LEDGER");
        return configured == null || configured.isBlank()
                ? Path.of("target", "offline-harness-ledger-v1.json")
                : Path.of(configured);
    }
}
