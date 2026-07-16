package org.rostilos.codecrow.analysisengine.telemetry;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

import org.rostilos.codecrow.analysisengine.execution.ImmutableExecutionManifest;

/**
 * Reconciles the Python analysis snapshot with Java persistence and delivery.
 * Python deliberately cannot emit the end-to-end terminal because those two
 * stages have not happened when its queue result is produced.
 */
public final class PipelineTelemetryFinalizer {
    private static final Pattern REVISION = Pattern.compile(
            "(?:[0-9a-f]{40}|[0-9a-f]{64})");
    private static final Pattern INDEX_VERSION = Pattern.compile(
            "(?:rag-disabled|rag-commit-(?:[0-9a-f]{40}|[0-9a-f]{64}))");
    private static final Set<String> REQUIRED_STAGES = Set.of(
            "acquisition",
            "retrieval",
            "generation",
            "pre_dedup",
            "post_dedup",
            "verification",
            "reconciliation",
            "persistence",
            "delivery");
    private static final Set<String> REQUIRED_VERSIONS = Set.of(
            "provider",
            "model",
            "prompt_version",
            "rules_version",
            "policy_version",
            "index_version");

    private PipelineTelemetryFinalizer() {
    }

    public record StageObservation(
            String name,
            String producer,
            String outcome,
            long durationMs,
            int itemCount,
            String reasonCode) {
        public StageObservation {
            if (name == null || name.isBlank() || producer == null || producer.isBlank()) {
                throw new IllegalArgumentException("stage identity is required");
            }
            if (!Set.of("complete", "partial", "failed", "skipped").contains(outcome)) {
                throw new IllegalArgumentException("stage outcome is invalid");
            }
            if (durationMs < 0 || itemCount < 0) {
                throw new IllegalArgumentException("stage counters must be non-negative");
            }
            if ("complete".equals(outcome) && reasonCode != null) {
                throw new IllegalArgumentException("complete stage cannot carry a reason");
            }
            if (!"complete".equals(outcome) && (reasonCode == null || reasonCode.isBlank())) {
                throw new IllegalArgumentException("non-complete stage requires a reason");
            }
        }

        Map<String, Object> toDocument() {
            Map<String, Object> stage = new LinkedHashMap<>();
            stage.put("name", name);
            stage.put("producer", producer);
            stage.put("outcome", outcome);
            stage.put("duration_ms", durationMs);
            stage.put("usage", zeroUsage());
            stage.put("candidates", Map.of(
                    "input", itemCount,
                    "produced", 0,
                    "retained", itemCount));
            stage.put("coverage", Map.of(
                    "inventory", 0,
                    "represented", 0,
                    "unrepresented", 0));
            stage.put("reason", reasonCode);
            return stage;
        }
    }

    public static Map<String, Object> finalizeDocument(
            Map<String, Object> telemetryDocument,
            List<StageObservation> javaStages,
            long totalDurationMs) {
        return finalizeDocument(
                telemetryDocument, javaStages, totalDurationMs, null, null);
    }

    /**
     * Finalizes a candidate trace only when its high-cardinality identity is
     * exactly the durable immutable manifest and selected index identity.
     */
    public static Map<String, Object> finalizeDocument(
            Map<String, Object> telemetryDocument,
            List<StageObservation> javaStages,
            long totalDurationMs,
            ImmutableExecutionManifest expectedManifest,
            String expectedIndexVersion) {
        if (telemetryDocument == null
                || !"pending_java".equals(telemetryDocument.get("finalizationState"))) {
            throw new IllegalArgumentException("a pending Python telemetry snapshot is required");
        }
        if (totalDurationMs < 0) {
            throw new IllegalArgumentException("terminal duration must be non-negative");
        }

        Map<String, Object> trace = mutableMap(telemetryDocument.get("trace"), "trace");
        String executionId = requireText(trace, "execution_id");
        String baseRevision = requireText(trace, "base_revision");
        String headRevision = requireText(trace, "head_revision");
        if (!REVISION.matcher(baseRevision).matches() || !REVISION.matcher(headRevision).matches()) {
            throw new IllegalArgumentException("exact hexadecimal comparison revisions are required");
        }
        Map<String, Object> versions = mutableMap(trace.get("versions"), "versions");
        for (String field : REQUIRED_VERSIONS) {
            requireText(versions, field);
        }
        String indexVersion = requireText(versions, "index_version");
        if (!INDEX_VERSION.matcher(indexVersion).matches()) {
            throw new IllegalArgumentException("exact RAG index_version is required");
        }
        if (expectedManifest != null) {
            if (!"rag-disabled".equals(expectedIndexVersion)) {
                throw new IllegalArgumentException(
                        "manifest-bound candidate index_version must be rag-disabled");
            }
            requireEqual(executionId, expectedManifest.executionId(), "execution_id");
            requireEqual(
                    requireText(trace, "artifact_manifest_digest"),
                    expectedManifest.artifactManifestDigest(),
                    "artifact_manifest_digest");
            requireEqual(baseRevision, expectedManifest.baseSha(), "base_revision");
            requireEqual(headRevision, expectedManifest.headSha(), "head_revision");
            requireEqual(
                    requireText(versions, "policy_version"),
                    expectedManifest.policyVersion(),
                    "policy_version");
            requireEqual(indexVersion, expectedIndexVersion, "index_version");
        }

        List<Object> combinedStages = mutableList(trace.get("stages"), "stages");
        for (StageObservation stage : javaStages == null ? List.<StageObservation>of() : javaStages) {
            combinedStages.add(stage.toDocument());
        }
        Set<String> stageNames = new LinkedHashSet<>();
        boolean persistenceFailed = false;
        boolean deliveryFailed = false;
        boolean javaStageFailed = false;
        boolean killSwitchCancellation = false;
        for (Object rawStage : combinedStages) {
            Map<String, Object> stage = mutableMap(rawStage, "stage");
            String name = requireText(stage, "name");
            String outcome = requireText(stage, "outcome");
            stageNames.add(name);
            persistenceFailed |= "persistence".equals(name) && "failed".equals(outcome);
            deliveryFailed |= "delivery".equals(name) && "failed".equals(outcome);
            javaStageFailed |= "failed".equals(outcome);
            killSwitchCancellation |= "skipped".equals(outcome)
                    && "kill_switch".equals(stage.get("reason"));
        }
        Set<String> missing = new LinkedHashSet<>(REQUIRED_STAGES);
        missing.removeAll(stageNames);
        if (!missing.isEmpty()) {
            throw new IllegalArgumentException("required pipeline stages are missing: " + missing);
        }

        String outcome = requireText(trace, "outcome");
        String reason = trace.get("reason") instanceof String text && !text.isBlank() ? text : null;
        if (killSwitchCancellation) {
            outcome = "cancelled";
            reason = "policy_kill_switch";
        } else if (persistenceFailed) {
            outcome = "failed";
            reason = "analysis_persistence_failed";
        } else if (deliveryFailed && "complete".equals(outcome)) {
            outcome = "partial";
            reason = "vcs_delivery_failed";
        } else if (javaStageFailed && "complete".equals(outcome)) {
            outcome = "partial";
            reason = "java_stage_failed";
        }
        if (!Set.of("complete", "partial", "failed", "cancelled").contains(outcome)) {
            throw new IllegalArgumentException("terminal outcome is invalid");
        }
        if (!"complete".equals(outcome) && reason == null) {
            throw new IllegalArgumentException("non-complete terminal requires a reason");
        }

        Map<String, Object> usage = mutableMap(trace.get("usage"), "usage");
        Map<String, Object> candidates = mutableMap(trace.get("candidates"), "candidates");
        Map<String, Object> coverage = mutableMap(trace.get("coverage"), "coverage");
        if ("complete".equals(outcome)) {
            requireZero(coverage, "unrepresented");
            requireZero(usage, "provider_usage_missing_calls");
            requireZero(usage, "cost_estimate_missing_calls");
        }

        trace.put("outcome", outcome);
        trace.put("reason", reason);
        trace.put("duration_ms", totalDurationMs);
        trace.put("stages", combinedStages);

        Map<String, String> labels = Map.of(
                "outcome", outcome,
                "policy_version", requireText(versions, "policy_version"),
                "provider", requireText(versions, "provider"));
        Map<String, Object> values = new LinkedHashMap<>();
        values.put("duration_ms", totalDurationMs);
        values.putAll(usage);
        values.put("candidate_input", requireNumber(candidates, "input"));
        values.put("candidate_produced", requireNumber(candidates, "produced"));
        values.put("candidate_retained", requireNumber(candidates, "retained"));
        values.put("coverage_inventory", requireNumber(coverage, "inventory"));
        values.put("coverage_represented", requireNumber(coverage, "represented"));
        values.put("coverage_unrepresented", requireNumber(coverage, "unrepresented"));

        Map<String, Object> finalized = new LinkedHashMap<>(telemetryDocument);
        finalized.put("finalizationState", "terminal");
        finalized.put("trace", trace);
        finalized.put("metric", Map.of(
                "name", "codecrow.review.execution.terminal",
                "labels", labels,
                "values", values));
        return finalized;
    }

    private static Map<String, Object> zeroUsage() {
        Map<String, Object> usage = new LinkedHashMap<>();
        for (String name : List.of(
                "requested_input_tokens",
                "requested_output_tokens",
                "provider_input_tokens",
                "provider_output_tokens",
                "provider_cache_read_tokens",
                "calls",
                "retries",
                "estimated_cost_microunits",
                "provider_usage_missing_calls",
                "cost_estimate_missing_calls")) {
            usage.put(name, 0);
        }
        return usage;
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> mutableMap(Object value, String field) {
        if (!(value instanceof Map<?, ?> raw)) {
            throw new IllegalArgumentException(field + " must be a map");
        }
        Map<String, Object> result = new LinkedHashMap<>();
        raw.forEach((key, item) -> result.put(String.valueOf(key), item));
        return result;
    }

    private static List<Object> mutableList(Object value, String field) {
        if (!(value instanceof List<?> raw)) {
            throw new IllegalArgumentException(field + " must be a list");
        }
        return new ArrayList<>(raw);
    }

    private static String requireText(Map<String, Object> values, String field) {
        Object value = values.get(field);
        if (!(value instanceof String text) || text.isBlank()) {
            throw new IllegalArgumentException(field + " is required");
        }
        return text;
    }

    private static Number requireNumber(Map<String, Object> values, String field) {
        Object value = values.get(field);
        if (!(value instanceof Number number) || number.longValue() < 0) {
            throw new IllegalArgumentException(field + " must be a non-negative number");
        }
        return number;
    }

    private static void requireZero(Map<String, Object> values, String field) {
        if (requireNumber(values, field).longValue() != 0) {
            throw new IllegalArgumentException("complete terminal has incomplete " + field);
        }
    }

    private static void requireEqual(String observed, String expected, String field) {
        if (!java.security.MessageDigest.isEqual(
                observed.getBytes(java.nio.charset.StandardCharsets.UTF_8),
                expected.getBytes(java.nio.charset.StandardCharsets.UTF_8))) {
            throw new IllegalArgumentException(field + " conflicts with immutable execution");
        }
    }
}
