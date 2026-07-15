package org.rostilos.codecrow.analysisengine.telemetry;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.analysisengine.telemetry.PipelineTelemetryFinalizer.StageObservation;

class PipelineTelemetryFinalizerTest {

    @Test
    void finalizesOnlyAfterJavaPersistenceAndDelivery() {
        Map<String, Object> finalized = PipelineTelemetryFinalizer.finalizeDocument(
                pendingSnapshot(),
                javaStages("complete", null),
                91L);

        assertThat(finalized).containsEntry("finalizationState", "terminal");
        @SuppressWarnings("unchecked")
        Map<String, Object> trace = (Map<String, Object>) finalized.get("trace");
        assertThat(trace).containsEntry("outcome", "complete").containsEntry("duration_ms", 91L);
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> stages = (List<Map<String, Object>>) trace.get("stages");
        assertThat(stages).extracting(stage -> stage.get("name"))
                .contains("acquisition", "retrieval", "persistence", "delivery");

        @SuppressWarnings("unchecked")
        Map<String, Object> metric = (Map<String, Object>) finalized.get("metric");
        @SuppressWarnings("unchecked")
        Map<String, String> labels = (Map<String, String>) metric.get("labels");
        assertThat(labels).containsOnly(
                org.assertj.core.api.Assertions.entry("outcome", "complete"),
                org.assertj.core.api.Assertions.entry("policy_version", "legacy-review-v1"),
                org.assertj.core.api.Assertions.entry("provider", "scripted"));
    }

    @Test
    void deliveryFailureMakesTheEndToEndTerminalPartial() {
        Map<String, Object> finalized = PipelineTelemetryFinalizer.finalizeDocument(
                pendingSnapshot(),
                javaStages("failed", "vcs_delivery_failed"),
                100L);

        @SuppressWarnings("unchecked")
        Map<String, Object> trace = (Map<String, Object>) finalized.get("trace");
        assertThat(trace)
                .containsEntry("outcome", "partial")
                .containsEntry("reason", "vcs_delivery_failed");
    }

    @Test
    void refusesTerminalWhenARequiredStageOrExactRevisionIsMissing() {
        List<StageObservation> missingDelivery = new ArrayList<>(javaStages("complete", null));
        missingDelivery.remove(missingDelivery.size() - 1);
        assertThatThrownBy(() -> PipelineTelemetryFinalizer.finalizeDocument(
                pendingSnapshot(), missingDelivery, 1L))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("required pipeline stages");

        Map<String, Object> invalid = pendingSnapshot();
        @SuppressWarnings("unchecked")
        Map<String, Object> trace = (Map<String, Object>) invalid.get("trace");
        trace.put("base_revision", "not-exact");
        assertThatThrownBy(() -> PipelineTelemetryFinalizer.finalizeDocument(
                invalid, javaStages("complete", null), 1L))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("exact hexadecimal");
    }

    @Test
    void reconcilesPersistenceCancellationAndOtherJavaFailuresTruthfully() {
        List<StageObservation> persistenceFailure = List.of(
                new StageObservation("acquisition", "java_vcs_diff", "complete", 1, 1, null),
                new StageObservation("retrieval", "java_rag_index", "complete", 1, 0, null),
                new StageObservation(
                        "persistence", "java_analysis_store", "failed", 1, 0,
                        "analysis_persistence_failed"),
                new StageObservation(
                        "delivery", "java_vcs_reporting", "skipped", 1, 0,
                        "upstream_failed"));
        assertOutcome(
                PipelineTelemetryFinalizer.finalizeDocument(
                        pendingSnapshot(), persistenceFailure, 1L),
                "failed",
                "analysis_persistence_failed");

        List<StageObservation> cancelled = List.of(
                new StageObservation("acquisition", "java_vcs_diff", "complete", 1, 1, null),
                new StageObservation("retrieval", "java_rag_index", "complete", 1, 0, null),
                new StageObservation(
                        "persistence", "java_analysis_store", "skipped", 1, 0,
                        "kill_switch"),
                new StageObservation(
                        "delivery", "java_vcs_reporting", "skipped", 1, 0,
                        "kill_switch"));
        assertOutcome(
                PipelineTelemetryFinalizer.finalizeDocument(
                        pendingSnapshot(), cancelled, 1L),
                "cancelled",
                "policy_kill_switch");

        List<StageObservation> retrievalFailure = List.of(
                new StageObservation("acquisition", "java_vcs_diff", "complete", 1, 1, null),
                new StageObservation(
                        "retrieval", "java_rag_index", "failed", 1, 0,
                        "rag_index_refresh_failed"),
                new StageObservation("persistence", "java_analysis_store", "complete", 1, 0, null),
                new StageObservation("delivery", "java_vcs_reporting", "complete", 1, 0, null));
        assertOutcome(
                PipelineTelemetryFinalizer.finalizeDocument(
                        pendingSnapshot(), retrievalFailure, 1L),
                "partial",
                "java_stage_failed");

        Map<String, Object> alreadyPartial = pendingSnapshot();
        trace(alreadyPartial).put("outcome", "partial");
        trace(alreadyPartial).put("reason", "coverage_incomplete");
        assertOutcome(
                PipelineTelemetryFinalizer.finalizeDocument(
                        alreadyPartial, retrievalFailure, 1L),
                "partial",
                "coverage_incomplete");
    }

    @Test
    void completeTerminalRejectsIncompleteCoverageOrUsageAccounting() {
        Map<String, Object> uncovered = pendingSnapshot();
        trace(uncovered).put(
                "coverage", Map.of("inventory", 1, "represented", 0, "unrepresented", 1));
        assertThatThrownBy(() -> PipelineTelemetryFinalizer.finalizeDocument(
                uncovered, javaStages("complete", null), 1L))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("unrepresented");

        Map<String, Object> providerUsageMissing = pendingSnapshot();
        Map<String, Object> providerUsage = new LinkedHashMap<>(usage());
        providerUsage.put("provider_usage_missing_calls", 1);
        trace(providerUsageMissing).put("usage", providerUsage);
        assertThatThrownBy(() -> PipelineTelemetryFinalizer.finalizeDocument(
                providerUsageMissing, javaStages("complete", null), 1L))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("provider_usage_missing_calls");

        Map<String, Object> costMissing = pendingSnapshot();
        Map<String, Object> costUsage = new LinkedHashMap<>(usage());
        costUsage.put("cost_estimate_missing_calls", 1);
        trace(costMissing).put("usage", costUsage);
        assertThatThrownBy(() -> PipelineTelemetryFinalizer.finalizeDocument(
                costMissing, javaStages("complete", null), 1L))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("cost_estimate_missing_calls");
    }

    @Test
    void validatesStageObservationsAndPendingDocumentShape() {
        assertThatThrownBy(() -> new StageObservation(null, "producer", "complete", 0, 0, null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("identity");
        assertThatThrownBy(() -> new StageObservation(" ", "producer", "complete", 0, 0, null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("identity");
        assertThatThrownBy(() -> new StageObservation("stage", null, "complete", 0, 0, null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("identity");
        assertThatThrownBy(() -> new StageObservation("stage", " ", "complete", 0, 0, null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("identity");
        assertThatThrownBy(() -> new StageObservation("stage", "producer", "unknown", 0, 0, null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("outcome");
        assertThatThrownBy(() -> new StageObservation("stage", "producer", "complete", -1, 0, null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("counters");
        assertThatThrownBy(() -> new StageObservation("stage", "producer", "complete", 0, -1, null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("counters");
        assertThatThrownBy(() -> new StageObservation(
                "stage", "producer", "complete", 0, 0, "unexpected"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("cannot carry");
        assertThatThrownBy(() -> new StageObservation("stage", "producer", "failed", 0, 0, null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("requires a reason");
        assertThatThrownBy(() -> new StageObservation("stage", "producer", "failed", 0, 0, " "))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("requires a reason");

        assertThatThrownBy(() -> PipelineTelemetryFinalizer.finalizeDocument(
                null, javaStages("complete", null), 0))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("pending");
        Map<String, Object> wrongState = pendingSnapshot();
        wrongState.put("finalizationState", "terminal");
        assertThatThrownBy(() -> PipelineTelemetryFinalizer.finalizeDocument(
                wrongState, javaStages("complete", null), 0))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("pending");
        assertThatThrownBy(() -> PipelineTelemetryFinalizer.finalizeDocument(
                pendingSnapshot(), javaStages("complete", null), -1))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("duration");
    }

    @Test
    void rejectsEveryMalformedTerminalBoundaryAndPreservesPriorPartialOutcome() {
        Map<String, Object> invalidHead = pendingSnapshot();
        trace(invalidHead).put("head_revision", "not-exact");
        assertThatThrownBy(() -> PipelineTelemetryFinalizer.finalizeDocument(
                invalidHead, javaStages("complete", null), 0))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("exact hexadecimal");

        assertThatThrownBy(() -> PipelineTelemetryFinalizer.finalizeDocument(
                pendingSnapshot(), null, 0))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("required pipeline stages");

        Map<String, Object> invalidTrace = pendingSnapshot();
        invalidTrace.put("trace", "not-a-map");
        assertThatThrownBy(() -> PipelineTelemetryFinalizer.finalizeDocument(
                invalidTrace, javaStages("complete", null), 0))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("trace must be a map");

        Map<String, Object> invalidStages = pendingSnapshot();
        trace(invalidStages).put("stages", "not-a-list");
        assertThatThrownBy(() -> PipelineTelemetryFinalizer.finalizeDocument(
                invalidStages, javaStages("complete", null), 0))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("stages must be a list");

        Map<String, Object> blankVersion = pendingSnapshot();
        Map<String, Object> versions = new LinkedHashMap<>(
                (Map<String, Object>) trace(blankVersion).get("versions"));
        versions.put("model", " ");
        trace(blankVersion).put("versions", versions);
        assertThatThrownBy(() -> PipelineTelemetryFinalizer.finalizeDocument(
                blankVersion, javaStages("complete", null), 0))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("model is required");

        Map<String, Object> inexactIndex = pendingSnapshot();
        Map<String, Object> inexactVersions = new LinkedHashMap<>(
                (Map<String, Object>) trace(inexactIndex).get("versions"));
        inexactVersions.put("index_version", "stale-index-v1");
        trace(inexactIndex).put("versions", inexactVersions);
        assertThatThrownBy(() -> PipelineTelemetryFinalizer.finalizeDocument(
                inexactIndex, javaStages("complete", null), 0))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("exact RAG index version");

        Map<String, Object> nonTextVersion = pendingSnapshot();
        Map<String, Object> typedVersions = new LinkedHashMap<>(
                (Map<String, Object>) trace(nonTextVersion).get("versions"));
        typedVersions.put("model", 7);
        trace(nonTextVersion).put("versions", typedVersions);
        assertThatThrownBy(() -> PipelineTelemetryFinalizer.finalizeDocument(
                nonTextVersion, javaStages("complete", null), 0))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("model is required");

        Map<String, Object> invalidOutcome = pendingSnapshot();
        trace(invalidOutcome).put("outcome", "unknown");
        assertThatThrownBy(() -> PipelineTelemetryFinalizer.finalizeDocument(
                invalidOutcome, javaStages("complete", null), 0))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("terminal outcome");

        Map<String, Object> unexplainedPartial = pendingSnapshot();
        trace(unexplainedPartial).put("outcome", "partial");
        trace(unexplainedPartial).put("reason", " ");
        assertThatThrownBy(() -> PipelineTelemetryFinalizer.finalizeDocument(
                unexplainedPartial, javaStages("complete", null), 0))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("requires a reason");

        Map<String, Object> priorPartial = pendingSnapshot();
        trace(priorPartial).put("outcome", "partial");
        trace(priorPartial).put("reason", "coverage_incomplete");
        assertOutcome(PipelineTelemetryFinalizer.finalizeDocument(
                priorPartial, javaStages("failed", "vcs_delivery_failed"), 0),
                "partial", "coverage_incomplete");

        Map<String, Object> nonNumericCandidate = pendingSnapshot();
        trace(nonNumericCandidate).put(
                "candidates", Map.of("input", "invalid", "produced", 0, "retained", 0));
        assertThatThrownBy(() -> PipelineTelemetryFinalizer.finalizeDocument(
                nonNumericCandidate, javaStages("complete", null), 0))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("input must be a non-negative number");

        Map<String, Object> negativeCandidate = pendingSnapshot();
        trace(negativeCandidate).put(
                "candidates", Map.of("input", -1, "produced", 0, "retained", 0));
        assertThatThrownBy(() -> PipelineTelemetryFinalizer.finalizeDocument(
                negativeCandidate, javaStages("complete", null), 0))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("input must be a non-negative number");
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> trace(Map<String, Object> document) {
        return (Map<String, Object>) document.get("trace");
    }

    @SuppressWarnings("unchecked")
    private static void assertOutcome(
            Map<String, Object> document,
            String outcome,
            String reason) {
        Map<String, Object> trace = (Map<String, Object>) document.get("trace");
        assertThat(trace)
                .containsEntry("outcome", outcome)
                .containsEntry("reason", reason);
    }

    private static List<StageObservation> javaStages(String deliveryOutcome, String deliveryReason) {
        return List.of(
                new StageObservation("acquisition", "java_vcs_diff", "complete", 1, 1, null),
                new StageObservation("retrieval", "java_rag_index", "complete", 1, 0, null),
                new StageObservation("persistence", "java_analysis_store", "complete", 1, 2, null),
                new StageObservation(
                        "delivery", "java_vcs_reporting", deliveryOutcome, 1, 2, deliveryReason));
    }

    private static Map<String, Object> pendingSnapshot() {
        Map<String, Object> trace = new LinkedHashMap<>();
        trace.put("execution_id", "execution-p004");
        trace.put("base_revision", "a".repeat(40));
        trace.put("head_revision", "b".repeat(40));
        trace.put("versions", Map.of(
                "provider", "scripted",
                "model", "fixture-v1",
                "prompt_version", "prompt-sha256-" + "1".repeat(64),
                "rules_version", "rules-sha256-" + "2".repeat(64),
                "policy_version", "legacy-review-v1",
                "index_version", "rag-commit-" + "c".repeat(40)));
        trace.put("outcome", "complete");
        trace.put("duration_ms", 10);
        trace.put("usage", usage());
        trace.put("candidates", Map.of("input", 0, "produced", 2, "retained", 2));
        trace.put("coverage", Map.of("inventory", 1, "represented", 1, "unrepresented", 0));
        trace.put("reason", null);
        trace.put("stages", List.of(
                stage("generation"),
                stage("pre_dedup"),
                stage("post_dedup"),
                stage("verification"),
                stage("reconciliation")));
        trace.put("model_calls", List.of());
        trace.put("tool_calls", List.of());
        trace.put("lineage", List.of());

        Map<String, Object> document = new LinkedHashMap<>();
        document.put("schemaVersion", 1);
        document.put("finalizationState", "pending_java");
        document.put("trace", trace);
        document.put("metric", null);
        document.put("sinkErrors", List.of());
        return document;
    }

    private static Map<String, Object> stage(String name) {
        Map<String, Object> stage = new LinkedHashMap<>();
        stage.put("name", name);
        stage.put("producer", "python");
        stage.put("outcome", "complete");
        stage.put("duration_ms", 1);
        stage.put("usage", usage());
        stage.put("candidates", Map.of("input", 0, "produced", 0, "retained", 0));
        stage.put("coverage", Map.of("inventory", 1, "represented", 1, "unrepresented", 0));
        stage.put("reason", null);
        return stage;
    }

    private static Map<String, Object> usage() {
        Map<String, Object> usage = new LinkedHashMap<>();
        usage.put("requested_input_tokens", 10);
        usage.put("requested_output_tokens", 5);
        usage.put("provider_input_tokens", 9);
        usage.put("provider_output_tokens", 4);
        usage.put("provider_cache_read_tokens", 0);
        usage.put("calls", 1);
        usage.put("retries", 0);
        usage.put("estimated_cost_microunits", 13);
        usage.put("provider_usage_missing_calls", 0);
        usage.put("cost_estimate_missing_calls", 0);
        return usage;
    }
}
