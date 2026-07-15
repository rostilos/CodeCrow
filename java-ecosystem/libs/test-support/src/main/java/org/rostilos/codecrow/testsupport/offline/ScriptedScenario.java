package org.rostilos.codecrow.testsupport.offline;

import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * Reusable fake behavior keyed by operation and per-operation call ordinal.
 * Independent operations may interleave without changing their scripted result.
 */
public final class ScriptedScenario {

    private static final Pattern SCENARIO_ID = Pattern.compile(
            "^[A-Za-z0-9](?:[A-Za-z0-9_. -]*[A-Za-z0-9])?$"
    );

    private final String scenarioId;
    private final String boundary;
    private final String target;
    private final List<ScriptedStep> schedule;
    private final Map<StepKey, ScriptedStep> stepsByKey;
    private final ExternalCallLedger ledger;
    private final Map<String, Integer> nextCallByOperation = new HashMap<>();
    private final Set<StepKey> consumed = new HashSet<>();

    public ScriptedScenario(
            String scenarioId,
            String boundary,
            String target,
            List<ScriptedStep> schedule,
            ExternalCallLedger ledger
    ) {
        this.scenarioId = requireScenarioText(scenarioId, "scenarioId");
        this.boundary = requireUnpaddedNonBlank(boundary, "boundary");
        this.target = requireUnpaddedNonBlank(target, "target");
        this.schedule = List.copyOf(schedule);
        this.ledger = Objects.requireNonNull(ledger, "ledger");

        Map<StepKey, ScriptedStep> indexed = new HashMap<>();
        Map<String, Integer> operationSizes = new HashMap<>();
        for (ScriptedStep step : this.schedule) {
            StepKey key = new StepKey(step.operation(), step.call());
            if (indexed.putIfAbsent(key, step) != null) {
                throw new IllegalArgumentException("duplicate scripted operation/call slot");
            }
            operationSizes.merge(step.operation(), 1, Integer::sum);
        }
        for (Map.Entry<String, Integer> operation : operationSizes.entrySet()) {
            for (int call = 1; call <= operation.getValue(); call++) {
                if (!indexed.containsKey(new StepKey(operation.getKey(), call))) {
                    throw new IllegalArgumentException("scripted call ordinals must be contiguous");
                }
            }
        }
        this.stepsByKey = Map.copyOf(indexed);
    }

    private static String requireUnpaddedNonBlank(String value, String field) {
        String required = Objects.requireNonNull(value, field);
        if (required.isBlank() || !required.equals(required.strip())) {
            throw new IllegalArgumentException(field + " must not be blank");
        }
        return required;
    }

    private static String requireScenarioText(String value, String field) {
        String required = Objects.requireNonNull(value, field);
        if (!SCENARIO_ID.matcher(required).matches()) {
            throw new IllegalArgumentException("invalid " + field);
        }
        return required;
    }

    public static ScriptedScenario fromDocument(
            ScriptedScenarioDocument document,
            String boundary,
            String target,
            ExternalCallLedger ledger
    ) {
        if (!"1.0".equals(document.schemaVersion())) {
            throw new IllegalArgumentException("unsupported scripted-scenario schema version");
        }
        return new ScriptedScenario(document.scenarioId(), boundary, target, document.steps(), ledger);
    }

    public synchronized Exchange next(String operation) {
        if (consumed.size() == schedule.size()) {
            throw new ScenarioExhaustedException(
                    "scripted scenario exhausted for boundary " + boundary
            );
        }
        int call = nextCallByOperation.getOrDefault(operation, 1);
        StepKey key = new StepKey(operation, call);
        ScriptedStep step = stepsByKey.get(key);
        if (step == null) {
            throw new IllegalStateException("unexpected scripted operation/call slot");
        }
        consumed.add(key);
        nextCallByOperation.put(operation, call + 1);
        ExternalCall externalCall = ledger.record(
                boundary,
                false,
                operation,
                step.kind().name().toLowerCase(Locale.ROOT),
                "SIMULATED",
                true,
                target
        );
        return new Exchange(externalCall.sequence(), step);
    }

    public synchronized int remaining() {
        return schedule.size() - consumed.size();
    }

    public ScriptedScenarioDocument document() {
        return new ScriptedScenarioDocument(scenarioId, "1.0", schedule);
    }

    public ScriptedScenario replay() {
        return new ScriptedScenario(scenarioId, boundary, target, schedule, ledger);
    }

    private record StepKey(String operation, int call) {
    }

    public record Exchange(long callSequence, ScriptedStep step) {
    }
}
