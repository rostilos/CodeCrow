package org.rostilos.codecrow.testsupport.offline;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.exc.UnrecognizedPropertyException;
import org.junit.jupiter.api.Test;

import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class ScriptedScenarioTest {

    @Test
    void schedulesEveryRequiredResponseAndFaultWithoutLoggingPayloads() {
        ExternalCallLedger ledger = new ExternalCallLedger();
        List<ScriptedStep> schedule = List.of(
                ScriptedStep.response("complete", 1, "plain secret payload"),
                ScriptedStep.structuredResponse("complete", 2, "{\"result\":\"structured secret\"}"),
                ScriptedStep.stream("complete", 3, List.of("chunk-one", "chunk-two")),
                ScriptedStep.rateLimit("complete", 4, Duration.ofSeconds(3)),
                ScriptedStep.malformed("complete", 5, "not-json secret"),
                ScriptedStep.timeout("complete", 6, Duration.ofMillis(250)),
                ScriptedStep.cancellation("complete", 7),
                ScriptedStep.overage("complete", 8, 100, 125),
                ScriptedStep.page("complete", 9, "page secret", "cursor-2"),
                ScriptedStep.duplicate("complete", 10, "delivery-7", 2),
                ScriptedStep.retryable("complete", 11, Duration.ofMillis(10))
        );
        ScriptedScenario scenario = new ScriptedScenario(
                "all-required-v1",
                "llm",
                "fake://provider",
                schedule,
                ledger
        );

        assertThat(scenario.remaining()).isEqualTo(schedule.size());
        for (ScriptedStep expected : schedule) {
            ScriptedScenario.Exchange exchange = scenario.next("complete");
            assertThat(exchange.step()).isEqualTo(expected);
            assertThat(exchange.callSequence()).isPositive();
        }

        assertThat(scenario.remaining()).isZero();
        assertThat(scenario.document()).isEqualTo(
                new ScriptedScenarioDocument("all-required-v1", "1.0", schedule)
        );
        assertThat(ledger.entries()).hasSize(schedule.size()).allSatisfy(call -> {
            assertThat(call.boundary()).isEqualTo("llm");
            assertThat(call.live()).isFalse();
            assertThat(call.operation()).isEqualTo("complete");
            assertThat(call.phase()).isEqualTo("SIMULATED");
            assertThat(call.simulated()).isTrue();
            assertThat(call.target()).isEqualTo("fake://provider");
        });
        assertThat(ledger.entries()).extracting(ExternalCall::outcome)
                .containsExactly(
                        "response", "structured", "stream", "rate_limit", "malformed", "timeout",
                        "cancellation", "overage", "page", "duplicate", "retryable"
                );
        assertThat(ledger.simulatedCallCount()).isEqualTo(schedule.size());
        ledger.assertZeroLiveCalls();
        assertThat(ledger.entries().toString())
                .doesNotContain("plain secret payload", "structured secret", "not-json secret", "page secret");
        assertThat(schedule.toString())
                .doesNotContain("plain secret payload", "structured secret", "not-json secret", "page secret");
        assertThatThrownBy(() -> scenario.next("complete"))
                .isInstanceOf(ScenarioExhaustedException.class)
                .hasMessageContaining("llm");
    }

    @Test
    void exposesTypedCanonicalFixtureDetailsAndRejectsInvalidSteps() {
        assertThat(ScriptedStep.response("op", 1, "ok").payload().asText()).isEqualTo("ok");
        assertThat(ScriptedStep.structuredResponse("op", 2, "{}").payload().asText()).isEqualTo("{}");
        assertThat(ScriptedStep.stream("op", 3, List.of("a", "b")).chunks())
                .extracting(JsonNode::asText)
                .containsExactly("a", "b");
        assertThat(ScriptedStep.rateLimit("op", 4, Duration.ofSeconds(2)).retryAfterSeconds())
                .isEqualTo(2.0);
        assertThat(ScriptedStep.malformed("op", 5, "{").payload().asText()).isEqualTo("{");
        assertThat(ScriptedStep.timeout("op", 6, Duration.ofMillis(9)).payload().asLong()).isEqualTo(9);
        assertThat(ScriptedStep.cancellation("op", 7)).satisfies(step -> {
            assertThat(step.payload()).isNull();
            assertThat(step.usage()).isEmpty();
            assertThat(step.chunks()).isEmpty();
        });
        assertThat(ScriptedStep.overage("op", 8, 5, 8).usage())
                .containsEntry("reserved_tokens", 5L)
                .containsEntry("reported_tokens", 8L);
        assertThat(ScriptedStep.page("op", 9, "body", null).nextCursor()).isNull();
        assertThat(ScriptedStep.page("op", 10, "body", "next").nextCursor()).isEqualTo("next");
        assertThat(ScriptedStep.duplicate("op", 11, "id-1", 2)).satisfies(step -> {
            assertThat(step.payload().get("delivery_id").asText()).isEqualTo("id-1");
            assertThat(step.duplicateCount()).isEqualTo(2);
        });
        assertThat(ScriptedStep.retryable("op", 12, Duration.ZERO).retryAfterSeconds()).isZero();
        assertThat(ScriptedStep.response("jira.page_2-test", 13, "ok").operation())
                .isEqualTo("jira.page_2-test");

        assertThatThrownBy(() -> ScriptedStep.overage("op", 1, 10, 9))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new ScriptedStep(
                " ", 1, ScriptedStep.Kind.RESPONSE, null, null, null, null, null, null
        )).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new ScriptedStep(
                " padded", 1, ScriptedStep.Kind.RESPONSE, null, null, null, null, null, null
        )).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new ScriptedStep(
                "padded\t", 1, ScriptedStep.Kind.RESPONSE, null, null, null, null, null, null
        )).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new ScriptedStep(
                "\u2003padded", 1, ScriptedStep.Kind.RESPONSE, null, null, null, null, null, null
        )).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new ScriptedStep(
                "a\nb", 1, ScriptedStep.Kind.RESPONSE, null, null, null, null, null, null
        )).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new ScriptedStep(
                "a\tb", 1, ScriptedStep.Kind.RESPONSE, null, null, null, null, null, null
        )).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new ScriptedStep(
                "a\u007fb", 1, ScriptedStep.Kind.RESPONSE, null, null, null, null, null, null
        )).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new ScriptedStep(
                "\u00a0op", 1, ScriptedStep.Kind.RESPONSE, null, null, null, null, null, null
        )).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new ScriptedStep(
                "op\u00a0", 1, ScriptedStep.Kind.RESPONSE, null, null, null, null, null, null
        )).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new ScriptedStep(
                "\ufeffop", 1, ScriptedStep.Kind.RESPONSE, null, null, null, null, null, null
        )).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new ScriptedStep(
                "op\ufeff", 1, ScriptedStep.Kind.RESPONSE, null, null, null, null, null, null
        )).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new ScriptedStep(
                "Uppercase", 1, ScriptedStep.Kind.RESPONSE, null, null, null, null, null, null
        )).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new ScriptedStep(
                "ordinary internal space", 1, ScriptedStep.Kind.RESPONSE,
                null, null, null, null, null, null
        )).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new ScriptedStep(
                "op", 0, ScriptedStep.Kind.RESPONSE, null, null, null, null, null, null
        )).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new ScriptedStep(
                "op", 1, ScriptedStep.Kind.RESPONSE, null,
                Map.of("input_tokens", -1L), List.of(), null, null, null
        )).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new ScriptedStep(
                "op", 1, ScriptedStep.Kind.RETRYABLE, null,
                Map.of(), List.of(), -0.1, null, null
        )).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new ScriptedStep(
                "op", 1, ScriptedStep.Kind.RETRYABLE, null,
                Map.of(), List.of(), Double.POSITIVE_INFINITY, null, null
        )).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new ScriptedStep(
                "op", 1, ScriptedStep.Kind.RETRYABLE, null,
                Map.of(), List.of(), Double.NaN, null, null
        )).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new ScriptedStep(
                "op", 1, ScriptedStep.Kind.DUPLICATE, null,
                Map.of(), List.of(), null, null, 0
        )).isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void replayStartsFromTheBeginningAndOperationMismatchDoesNotConsumeTheFixture() {
        ExternalCallLedger ledger = new ExternalCallLedger();
        List<String> chunks = new ArrayList<>(List.of("first"));
        ScriptedStep stream = ScriptedStep.stream("embed", 1, chunks);
        ScriptedScenario original = new ScriptedScenario(
                "embedding-v1",
                "embedding",
                "fake://embedding",
                List.of(stream, ScriptedStep.response("embed", 2, "done")),
                ledger
        );

        chunks.add("must-not-leak");
        assertThatThrownBy(() -> original.next("wrong"))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("slot");
        assertThat(original.remaining()).isEqualTo(2);
        assertThat(original.next("embed").step().chunks())
                .extracting(JsonNode::asText)
                .containsExactly("first");

        ScriptedScenario replay = original.replay();
        assertThat(replay.next("embed").step()).isEqualTo(stream);
        assertThat(replay.next("embed").step())
                .isEqualTo(ScriptedStep.response("embed", 2, "done"));
        assertThat(replay.remaining()).isZero();
    }

    @Test
    void consumesTheSingleSharedCrossLanguageScenarioGolden() throws Exception {
        Path goldenPath = SharedFixtureLocator.locate(
                "tools/offline-harness/fixtures/golden/scripted-scenario-v1.json"
        );
        ObjectMapper objectMapper = new ObjectMapper();
        assertThat(objectMapper.isEnabled(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES))
                .isTrue();
        JsonNode goldenTree = objectMapper.readTree(goldenPath.toFile());
        ScriptedScenarioDocument golden = objectMapper.treeToValue(
                goldenTree,
                ScriptedScenarioDocument.class
        );
        ExternalCallLedger ledger = new ExternalCallLedger();
        ScriptedScenario scenario = ScriptedScenario.fromDocument(
                golden,
                "provider",
                "fake-provider:24117",
                ledger
        );

        for (ScriptedStep step : golden.steps()) {
            assertThat(scenario.next(step.operation()).step()).isEqualTo(step);
        }

        assertThat(scenario.document()).isEqualTo(golden);
        JsonNode serializedGolden = objectMapper.valueToTree(golden);
        assertThat(serializedGolden.get("scenario_id").asText())
                .isEqualTo(goldenTree.get("scenario_id").asText());
        assertThat(serializedGolden.get("schema_version").asText())
                .isEqualTo(goldenTree.get("schema_version").asText());
        assertThat(serializedGolden.get("steps").size())
                .isEqualTo(goldenTree.get("steps").size());
        assertThat(ledger.entries()).extracting(ExternalCall::outcome)
                .containsExactly("structured", "stream", "page", "rate_limit", "duplicate");
        ledger.assertZeroLiveCalls();
        assertThatThrownBy(() -> ScriptedScenario.fromDocument(
                new ScriptedScenarioDocument("future", "2.0", List.of()),
                "provider",
                "fake-provider:24117",
                new ExternalCallLedger()
        )).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> objectMapper.readValue("""
                {"scenario_id":"unknown-envelope","schema_version":"1.0","steps":[],"unknown":true}
                """, ScriptedScenarioDocument.class))
                .isInstanceOf(UnrecognizedPropertyException.class);
        assertThatThrownBy(() -> objectMapper.readValue("""
                {"operation":"complete","call":1,"kind":"response","unknown":true}
                """, ScriptedStep.class))
                .isInstanceOf(UnrecognizedPropertyException.class);
    }

    @Test
    void keysCallsPerOperationSoInterleavingCannotChangeResults() {
        ExternalCallLedger ledger = new ExternalCallLedger();
        ScriptedScenario scenario = new ScriptedScenario(
                "interleaved-v1",
                "provider",
                "fake-provider:24117",
                List.of(
                        ScriptedStep.response("alpha", 1, "a1"),
                        ScriptedStep.response("beta", 1, "b1"),
                        ScriptedStep.response("alpha", 2, "a2"),
                        ScriptedStep.response("beta", 2, "b2")
                ),
                ledger
        );

        assertThat(scenario.next("beta").step().payload().asText()).isEqualTo("b1");
        assertThat(scenario.next("alpha").step().payload().asText()).isEqualTo("a1");
        assertThat(scenario.next("beta").step().payload().asText()).isEqualTo("b2");
        assertThat(scenario.next("alpha").step().payload().asText()).isEqualTo("a2");
        assertThat(scenario.remaining()).isZero();

        assertThatThrownBy(() -> new ScriptedScenario(
                "duplicate-v1",
                "provider",
                "fake-provider:24117",
                List.of(
                        ScriptedStep.response("alpha", 1, "first"),
                        ScriptedStep.response("alpha", 1, "duplicate")
                ),
                new ExternalCallLedger()
        )).isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("duplicate");
        assertThatThrownBy(() -> new ScriptedScenario(
                "gap-v1",
                "provider",
                "fake-provider:24117",
                List.of(ScriptedStep.response("alpha", 2, "gap")),
                new ExternalCallLedger()
        )).isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("contiguous");
    }

    @Test
    void rejectsBlankScenarioIdentityAndBoundaryConfigurationAtConstruction() {
        ScriptedStep step = ScriptedStep.response("complete", 1, "ok");

        assertThatThrownBy(() -> new ScriptedScenario(
                " ", "provider", "fake-provider:24117", List.of(step), new ExternalCallLedger()
        )).isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("scenarioId");
        assertThatThrownBy(() -> new ScriptedScenario(
                "scenario-v1", "\t", "fake-provider:24117", List.of(step), new ExternalCallLedger()
        )).isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("boundary");
        assertThatThrownBy(() -> new ScriptedScenario(
                "scenario-v1", "provider", "\n", List.of(step), new ExternalCallLedger()
        )).isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("target");
        assertThatThrownBy(() -> new ScriptedScenario(
                " scenario-v1", "provider", "fake-provider:24117", List.of(step), new ExternalCallLedger()
        )).isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("scenarioId");
        assertThatThrownBy(() -> new ScriptedScenario(
                "\u2003scenario-v1", "provider", "fake-provider:24117",
                List.of(step), new ExternalCallLedger()
        )).isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("scenarioId");
        assertThatThrownBy(() -> new ScriptedScenario(
                "a\nb", "provider", "fake-provider:24117", List.of(step), new ExternalCallLedger()
        )).isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("scenarioId");
        assertThatThrownBy(() -> new ScriptedScenario(
                "a\tb", "provider", "fake-provider:24117", List.of(step), new ExternalCallLedger()
        )).isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("scenarioId");
        assertThatThrownBy(() -> new ScriptedScenario(
                "a\u007fb", "provider", "fake-provider:24117", List.of(step), new ExternalCallLedger()
        )).isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("scenarioId");
        assertThatThrownBy(() -> new ScriptedScenario(
                "\u00a0Scenario", "provider", "fake-provider:24117",
                List.of(step), new ExternalCallLedger()
        )).isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("scenarioId");
        assertThatThrownBy(() -> new ScriptedScenario(
                "Scenario\u00a0", "provider", "fake-provider:24117",
                List.of(step), new ExternalCallLedger()
        )).isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("scenarioId");
        assertThatThrownBy(() -> new ScriptedScenario(
                "\ufeffScenario", "provider", "fake-provider:24117",
                List.of(step), new ExternalCallLedger()
        )).isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("scenarioId");
        assertThatThrownBy(() -> new ScriptedScenario(
                "Scenario\ufeff", "provider", "fake-provider:24117",
                List.of(step), new ExternalCallLedger()
        )).isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("scenarioId");
        assertThatThrownBy(() -> new ScriptedScenario(
                "Scénario", "provider", "fake-provider:24117",
                List.of(step), new ExternalCallLedger()
        )).isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("scenarioId");
        assertThatThrownBy(() -> new ScriptedScenario(
                "-Scenario", "provider", "fake-provider:24117",
                List.of(step), new ExternalCallLedger()
        )).isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("scenarioId");
        assertThatThrownBy(() -> new ScriptedScenario(
                "Scenario.", "provider", "fake-provider:24117",
                List.of(step), new ExternalCallLedger()
        )).isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("scenarioId");
        assertThatThrownBy(() -> new ScriptedScenario(
                "scenario-v1", "provider ", "fake-provider:24117", List.of(step), new ExternalCallLedger()
        )).isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("boundary");
        assertThatThrownBy(() -> new ScriptedScenario(
                "scenario-v1", "provider", " fake-provider:24117", List.of(step), new ExternalCallLedger()
        )).isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("target");
        assertThatThrownBy(() -> ScriptedScenario.fromDocument(
                new ScriptedScenarioDocument("", "1.0", List.of(step)),
                "provider",
                "fake-provider:24117",
                new ExternalCallLedger()
        )).isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("scenarioId");

        ScriptedStep caseSensitiveStep = ScriptedStep.response("complete", 1, "ok");
        ScriptedScenario caseSensitive = new ScriptedScenario(
                "Scenario-V1", "Provider", "Fake-Provider:24117",
                List.of(caseSensitiveStep), new ExternalCallLedger()
        );
        assertThat(caseSensitive.next("complete").step().operation()).isEqualTo("complete");
        assertThat(caseSensitive.document().scenarioId()).isEqualTo("Scenario-V1");

        ScriptedScenario ordinaryInternalSpace = new ScriptedScenario(
                "Scenario.Name_1-V2 With Space", "Provider", "Fake-Provider:24117",
                List.of(step), new ExternalCallLedger()
        );
        assertThat(ordinaryInternalSpace.document().scenarioId())
                .isEqualTo("Scenario.Name_1-V2 With Space");
    }
}
