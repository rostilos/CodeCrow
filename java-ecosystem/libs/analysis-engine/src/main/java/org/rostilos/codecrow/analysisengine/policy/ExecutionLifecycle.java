package org.rostilos.codecrow.analysisengine.policy;

import java.util.Objects;

/** Thread-safe local cancellation scaffold; P1 replaces it with the durable ledger. */
public final class ExecutionLifecycle {
    private final PolicyExecution execution;
    private ExecutionLifecycleState state = ExecutionLifecycleState.CREATED;

    public ExecutionLifecycle(PolicyExecution execution) {
        this.execution = Objects.requireNonNull(execution, "execution");
    }

    public PolicyExecution execution() {
        return execution;
    }

    public synchronized ExecutionLifecycleState state() {
        return state;
    }

    public synchronized boolean start() {
        if (state != ExecutionLifecycleState.CREATED) {
            return false;
        }
        state = ExecutionLifecycleState.RUNNING;
        return true;
    }

    public synchronized boolean complete() {
        if (state == ExecutionLifecycleState.COMPLETED) {
            return true;
        }
        if (state != ExecutionLifecycleState.CREATED
                && state != ExecutionLifecycleState.RUNNING) {
            return false;
        }
        state = ExecutionLifecycleState.COMPLETED;
        return true;
    }

    public synchronized boolean fail() {
        if (terminal(state)) {
            return false;
        }
        state = ExecutionLifecycleState.FAILED;
        return true;
    }

    public synchronized boolean reconcileKillSwitch(ExecutionPolicyConfig currentConfig) {
        Objects.requireNonNull(currentConfig, "currentConfig");
        boolean shouldCancel = currentConfig.stopNewWork()
                || (currentConfig.candidateKillSwitch() && execution.candidatePath());
        if (!shouldCancel) {
            return false;
        }
        if (terminal(state) || state == ExecutionLifecycleState.CANCELLATION_REQUESTED) {
            return false;
        }
        state = ExecutionLifecycleState.CANCELLATION_REQUESTED;
        return true;
    }

    public synchronized boolean markCancelled() {
        if (state != ExecutionLifecycleState.CANCELLATION_REQUESTED) {
            return false;
        }
        state = ExecutionLifecycleState.CANCELLED;
        return true;
    }

    private boolean terminal(ExecutionLifecycleState value) {
        return value == ExecutionLifecycleState.CANCELLED
                || value == ExecutionLifecycleState.COMPLETED
                || value == ExecutionLifecycleState.FAILED;
    }
}
