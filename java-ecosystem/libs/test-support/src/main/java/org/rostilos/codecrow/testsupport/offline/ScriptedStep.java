package org.rostilos.codecrow.testsupport.offline;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonPropertyOrder;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.JsonNodeFactory;

import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.regex.Pattern;

/**
 * One canonical v1 protocol response or fault. Payloads remain available to a
 * fake adapter but are intentionally omitted from {@link #toString()}.
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
@JsonPropertyOrder({
        "operation", "call", "kind", "payload", "usage", "chunks",
        "retry_after_seconds", "next_cursor", "duplicate_count"
})
public record ScriptedStep(
        String operation,
        int call,
        Kind kind,
        JsonNode payload,
        @JsonInclude(JsonInclude.Include.NON_EMPTY) Map<String, Long> usage,
        @JsonInclude(JsonInclude.Include.NON_EMPTY) List<JsonNode> chunks,
        @JsonProperty("retry_after_seconds") Double retryAfterSeconds,
        @JsonProperty("next_cursor") String nextCursor,
        @JsonProperty("duplicate_count") Integer duplicateCount
) {
    private static final Pattern OPERATION = Pattern.compile("^[a-z][a-z0-9_.-]*$");

    public ScriptedStep {
        Objects.requireNonNull(operation, "operation");
        Objects.requireNonNull(kind, "kind");
        if (!OPERATION.matcher(operation).matches()) {
            throw new IllegalArgumentException("invalid scripted operation");
        }
        if (call < 1) {
            throw new IllegalArgumentException("scripted call must be at least 1");
        }
        usage = usage == null ? Map.of() : Map.copyOf(usage);
        chunks = chunks == null ? List.of() : List.copyOf(chunks);
        if (usage.values().stream().anyMatch(value -> value < 0)) {
            throw new IllegalArgumentException("scripted usage must not be negative");
        }
        if (retryAfterSeconds != null) {
            if (!Double.isFinite(retryAfterSeconds) || retryAfterSeconds < 0) {
                throw new IllegalArgumentException("scripted retry delay must be finite and not negative");
            }
        }
        if (duplicateCount != null && duplicateCount < 1) {
            throw new IllegalArgumentException("scripted duplicate count must be at least 1");
        }
    }

    public static ScriptedStep response(String operation, int call, String payload) {
        return step(operation, call, Kind.RESPONSE, text(payload), Map.of(), List.of(), null, null, null);
    }

    public static ScriptedStep structuredResponse(String operation, int call, String payload) {
        return step(operation, call, Kind.STRUCTURED, text(payload), Map.of(), List.of(), null, null, null);
    }

    public static ScriptedStep stream(String operation, int call, List<String> chunks) {
        return step(
                operation,
                call,
                Kind.STREAM,
                null,
                Map.of(),
                chunks.stream().map(JsonNodeFactory.instance::textNode).map(JsonNode.class::cast).toList(),
                null,
                null,
                null
        );
    }

    public static ScriptedStep rateLimit(String operation, int call, Duration retryAfter) {
        return step(operation, call, Kind.RATE_LIMIT, null, Map.of(), List.of(),
                seconds(retryAfter), null, null);
    }

    public static ScriptedStep malformed(String operation, int call, String payload) {
        return step(operation, call, Kind.MALFORMED, text(payload), Map.of(), List.of(), null, null, null);
    }

    public static ScriptedStep timeout(String operation, int call, Duration timeout) {
        return step(
                operation,
                call,
                Kind.TIMEOUT,
                JsonNodeFactory.instance.numberNode(timeout.toMillis()),
                Map.of(),
                List.of(),
                null,
                null,
                null
        );
    }

    public static ScriptedStep cancellation(String operation, int call) {
        return step(operation, call, Kind.CANCELLATION, null, Map.of(), List.of(), null, null, null);
    }

    public static ScriptedStep overage(
            String operation,
            int call,
            long reservedTokens,
            long reportedTokens
    ) {
        if (reportedTokens <= reservedTokens) {
            throw new IllegalArgumentException("reported tokens must exceed the reservation");
        }
        return step(
                operation,
                call,
                Kind.OVERAGE,
                null,
                Map.of("reserved_tokens", reservedTokens, "reported_tokens", reportedTokens),
                List.of(),
                null,
                null,
                null
        );
    }

    public static ScriptedStep page(String operation, int call, String payload, String nextCursor) {
        return step(operation, call, Kind.PAGE, text(payload), Map.of(), List.of(), null, nextCursor, null);
    }

    public static ScriptedStep duplicate(
            String operation,
            int call,
            String deliveryId,
            int duplicateCount
    ) {
        JsonNode payload = JsonNodeFactory.instance.objectNode().put("delivery_id", deliveryId);
        return step(operation, call, Kind.DUPLICATE, payload, Map.of(), List.of(), null, null, duplicateCount);
    }

    public static ScriptedStep retryable(String operation, int call, Duration retryAfter) {
        return step(operation, call, Kind.RETRYABLE, null, Map.of(), List.of(),
                seconds(retryAfter), null, null);
    }

    private static ScriptedStep step(
            String operation,
            int call,
            Kind kind,
            JsonNode payload,
            Map<String, Long> usage,
            List<JsonNode> chunks,
            Double retryAfterSeconds,
            String nextCursor,
            Integer duplicateCount
    ) {
        return new ScriptedStep(
                operation,
                call,
                kind,
                payload,
                usage,
                chunks,
                retryAfterSeconds,
                nextCursor,
                duplicateCount
        );
    }

    private static JsonNode text(String value) {
        return JsonNodeFactory.instance.textNode(value);
    }

    private static double seconds(Duration duration) {
        return duration.toMillis() / 1000.0;
    }

    @Override
    public String toString() {
        return "ScriptedStep[operation=" + operation
                + ", call=" + call
                + ", kind=" + kind
                + ", chunkCount=" + chunks.size() + "]";
    }

    public enum Kind {
        @JsonProperty("response")
        RESPONSE,
        @JsonProperty("structured")
        STRUCTURED,
        @JsonProperty("stream")
        STREAM,
        @JsonProperty("rate_limit")
        RATE_LIMIT,
        @JsonProperty("malformed")
        MALFORMED,
        @JsonProperty("timeout")
        TIMEOUT,
        @JsonProperty("cancellation")
        CANCELLATION,
        @JsonProperty("overage")
        OVERAGE,
        @JsonProperty("page")
        PAGE,
        @JsonProperty("duplicate")
        DUPLICATE,
        @JsonProperty("retryable")
        RETRYABLE
    }
}
