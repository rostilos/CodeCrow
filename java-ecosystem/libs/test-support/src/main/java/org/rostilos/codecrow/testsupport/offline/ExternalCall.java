package org.rostilos.codecrow.testsupport.offline;

import com.fasterxml.jackson.annotation.JsonPropertyOrder;

import java.util.Objects;

/**
 * One redacted external-boundary observation emitted by an offline test.
 */
@JsonPropertyOrder({
        "boundary", "live", "operation", "outcome", "phase", "sequence", "simulated", "target"
})
public record ExternalCall(
        String boundary,
        boolean live,
        String operation,
        String outcome,
        String phase,
        long sequence,
        boolean simulated,
        String target
) {
    public ExternalCall {
        Objects.requireNonNull(boundary, "boundary");
        Objects.requireNonNull(operation, "operation");
        Objects.requireNonNull(outcome, "outcome");
        Objects.requireNonNull(phase, "phase");
        Objects.requireNonNull(target, "target");
    }
}
