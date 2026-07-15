package org.rostilos.codecrow.analysisengine.policy;

import org.springframework.core.env.Environment;
import org.springframework.stereotype.Service;

import java.time.Clock;
import java.util.Arrays;
import java.util.LinkedHashSet;
import java.util.Locale;
import java.util.Objects;
import java.util.Set;

/**
 * Reads all flags once per new execution and delegates to the immutable selector.
 * Defaults are deliberately legacy and zero-percent active rollout.
 */
@Service
public class ExecutionPolicyRuntime {
    public static final String MODE_PROPERTY = "codecrow.analysis.policy.mode";
    public static final String CANDIDATE_VERSION_PROPERTY =
            "codecrow.analysis.policy.candidate-version";
    public static final String KNOWN_VERSIONS_PROPERTY =
            "codecrow.analysis.policy.known-versions";
    public static final String ROLLOUT_BASIS_POINTS_PROPERTY =
            "codecrow.analysis.policy.rollout-basis-points";
    public static final String ROLLOUT_SALT_PROPERTY =
            "codecrow.analysis.policy.rollout-salt";
    public static final String CONFIG_REVISION_PROPERTY =
            "codecrow.analysis.policy.config-revision";
    public static final String STOP_NEW_WORK_PROPERTY =
            "codecrow.analysis.policy.stop-new-work";
    public static final String CANDIDATE_KILL_SWITCH_PROPERTY =
            "codecrow.analysis.policy.candidate-kill-switch";

    private final Environment environment;
    private final ExecutionControlStore store;
    private final Clock clock;

    public ExecutionPolicyRuntime(Environment environment, ExecutionControlStore store) {
        this(environment, store, Clock.systemUTC());
    }

    ExecutionPolicyRuntime(
            Environment environment,
            ExecutionControlStore store,
            Clock clock) {
        this.environment = Objects.requireNonNull(environment, "environment");
        this.store = Objects.requireNonNull(store, "store");
        this.clock = Objects.requireNonNull(clock, "clock");
    }

    public FrozenExecutionPlan freeze(
            String executionId,
            StableRolloutKey rolloutKey) {
        ExecutionPolicyConfig snapshot = currentConfig();
        ExecutionPolicyControlPlane controlPlane = new ExecutionPolicyControlPlane(
                knownPolicyVersions(), store, clock);
        return controlPlane.freeze(executionId, rolloutKey, snapshot);
    }

    public ExecutionPolicyConfig currentConfig() {
        String modeValue = property(MODE_PROPERTY, "legacy").toUpperCase(Locale.ROOT);
        ExecutionMode mode;
        try {
            mode = ExecutionMode.valueOf(modeValue);
        } catch (IllegalArgumentException error) {
            throw new IllegalArgumentException("Unknown execution policy mode: " + modeValue, error);
        }
        String candidateVersion = property(CANDIDATE_VERSION_PROPERTY, "candidate-review-v1");
        int rolloutBasisPoints;
        try {
            rolloutBasisPoints = Integer.parseInt(property(ROLLOUT_BASIS_POINTS_PROPERTY, "0"));
        } catch (NumberFormatException error) {
            throw new IllegalArgumentException("rollout basis points must be an integer", error);
        }
        String salt = property(ROLLOUT_SALT_PROPERTY, "codecrow-project-rollout-v1");
        boolean stopNewWork = booleanProperty(STOP_NEW_WORK_PROPERTY, false);
        boolean candidateKillSwitch = booleanProperty(CANDIDATE_KILL_SWITCH_PROPERTY, false);
        String explicitRevision = environment.getProperty(CONFIG_REVISION_PROPERTY);
        String configuredKnownVersions = property(
                KNOWN_VERSIONS_PROPERTY,
                ExecutionPolicyControlPlane.LEGACY_POLICY_VERSION + ",candidate-review-v1");
        String revision = explicitRevision == null || explicitRevision.isBlank()
                ? "cfg-" + sha256(String.join("\n",
                        mode.name(),
                        candidateVersion,
                        configuredKnownVersions,
                        Integer.toString(rolloutBasisPoints),
                        salt,
                        Boolean.toString(stopNewWork),
                        Boolean.toString(candidateKillSwitch))).substring(0, 24)
                : explicitRevision.trim();
        return new ExecutionPolicyConfig(
                revision,
                mode,
                candidateVersion,
                rolloutBasisPoints,
                salt,
                stopNewWork,
                candidateKillSwitch);
    }

    public Set<String> knownPolicyVersions() {
        String configured = property(
                KNOWN_VERSIONS_PROPERTY,
                ExecutionPolicyControlPlane.LEGACY_POLICY_VERSION + ",candidate-review-v1");
        Set<String> versions = new LinkedHashSet<>();
        Arrays.stream(configured.split(","))
                .map(String::trim)
                .filter(value -> !value.isEmpty())
                .forEach(versions::add);
        versions.add(ExecutionPolicyControlPlane.LEGACY_POLICY_VERSION);
        return Set.copyOf(versions);
    }

    public PublicationFence publicationFence() {
        return new PublicationFence(store);
    }

    public ExecutionArtifactWriter artifactWriter() {
        return new ExecutionArtifactWriter(store, clock);
    }

    private boolean booleanProperty(String name, boolean defaultValue) {
        String raw = environment.getProperty(name);
        if (raw == null || raw.isBlank()) {
            return defaultValue;
        }
        if ("true".equalsIgnoreCase(raw)) {
            return true;
        }
        if ("false".equalsIgnoreCase(raw)) {
            return false;
        }
        throw new IllegalArgumentException(name + " must be true or false");
    }

    private String property(String name, String defaultValue) {
        String value = environment.getProperty(name);
        return value == null || value.isBlank() ? defaultValue : value.trim();
    }

    private String sha256(String value) {
        return PolicyHashing.sha256(value);
    }
}
