package org.rostilos.codecrow.core.model.reconcile;

/**
 * Status of a reconciliation task queued via the web-server
 * and executed by the pipeline-agent.
 */
public enum ReconcileTaskStatus {
    PENDING,
    IN_PROGRESS,
    COMPLETED,
    FAILED
}
