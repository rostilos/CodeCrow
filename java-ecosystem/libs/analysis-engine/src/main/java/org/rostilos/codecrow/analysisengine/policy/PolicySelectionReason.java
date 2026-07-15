package org.rostilos.codecrow.analysisengine.policy;

/** Bounded, auditable reasons for selecting an execution path. */
public enum PolicySelectionReason {
    LEGACY_CONFIGURED,
    SHADOW_LEGACY_PRIMARY,
    SHADOW_CANDIDATE,
    ACTIVE_ROLLOUT_SELECTED,
    ACTIVE_ROLLOUT_NOT_SELECTED,
    CANDIDATE_KILL_SWITCH_ROLLBACK
}
