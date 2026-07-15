package org.rostilos.codecrow.testsupport.offline;

import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonPropertyOrder;

import java.util.List;

/** Canonical cross-language external-call ledger schema version 1.0. */
@JsonPropertyOrder({"schema_version", "live_call_count", "simulated_call_count", "calls"})
public record ExternalCallLedgerDocument(
        @JsonProperty("schema_version") String schemaVersion,
        @JsonProperty("live_call_count") long liveCallCount,
        @JsonProperty("simulated_call_count") long simulatedCallCount,
        List<ExternalCall> calls
) {
    public ExternalCallLedgerDocument {
        calls = List.copyOf(calls);
    }
}
