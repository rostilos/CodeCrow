package org.rostilos.codecrow.testsupport.offline;

import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonPropertyOrder;

import java.util.List;
import java.util.Objects;

/** Canonical cross-language deterministic scenario schema version 1.0. */
@JsonPropertyOrder({"scenario_id", "schema_version", "steps"})
public record ScriptedScenarioDocument(
        @JsonProperty("scenario_id") String scenarioId,
        @JsonProperty("schema_version") String schemaVersion,
        List<ScriptedStep> steps
) {
    public ScriptedScenarioDocument {
        Objects.requireNonNull(scenarioId, "scenarioId");
        Objects.requireNonNull(schemaVersion, "schemaVersion");
        steps = List.copyOf(steps);
    }
}
