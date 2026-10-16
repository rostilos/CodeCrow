package org.rostilos.codecrow.pipelineagent.qadoc;

import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.project.Project;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import java.util.*;
import java.util.function.Consumer;

/** Serializes PR replacement and task accumulation through publication, without a long database transaction. */
@Service
public class QaDocOperationService {
    private static final Logger log = LoggerFactory.getLogger(QaDocOperationService.class);
    private final AnalysisLockService locks;
    public QaDocOperationService(AnalysisLockService locks) { this.locks = locks; }

    public Operation open(Project project, Long pr, String task, Consumer<Map<String, Object>> events) {
        var operation = new Operation();
        try {
            // Every caller takes PR before task, including when a PR changes its linked task.
            for (String scope : List.of("qa-pr:" + pr, "qa-task:" + task.toUpperCase(Locale.ROOT))) {
                String key = locks.acquireLockWithWait(project, scope, AnalysisLockType.QA_DOCUMENTATION, null, pr, events)
                        .orElseThrow(() -> new IllegalStateException("QA documentation is still running for this PR or task; retry is required"));
                operation.leases.put(key, null);
                operation.leases.put(key, locks.maintainLockLease(key, locks.getLeaseMinutes(AnalysisLockType.QA_DOCUMENTATION)));
            }
            operation.assertOwnership();
            return operation;
        } catch (RuntimeException failure) {
            operation.close();
            throw failure;
        }
    }

    public final class Operation implements AutoCloseable {
        private final Map<String, AnalysisLockService.LockLease> leases = new LinkedHashMap<>();
        private boolean closed;
        public void assertOwnership() {
            if (closed) throw new IllegalStateException("QA documentation operation is already closed");
            for (var lease : leases.values()) {
                if (!lease.confirmOwnership()) throw new IllegalStateException("QA documentation lease was lost; publication deferred");
            }
        }
        public <T> T commit(java.util.function.Supplier<T> mutation) {
            if (closed) throw new IllegalStateException("QA documentation operation is already closed");
            return locks.withOwnedLocks(leases.keySet(), mutation);
        }
        @Override public void close() {
            if (closed) return;
            closed = true;
            var keys = new ArrayList<>(leases.keySet()); Collections.reverse(keys);
            for (String key : keys) {
                try { if (leases.get(key) != null) leases.get(key).close(); }
                catch (Exception error) { log.warn("QA lease heartbeat cleanup failed for {}: {}", key, error.getMessage()); }
                try { locks.releaseLock(key); }
                catch (Exception error) { log.warn("QA lease cleanup deferred to expiry for {}: {}", key, error.getMessage()); }
            }
        }
    }
}
