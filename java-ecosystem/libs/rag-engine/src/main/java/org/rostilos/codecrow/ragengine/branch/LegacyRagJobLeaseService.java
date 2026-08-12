package org.rostilos.codecrow.ragengine.branch;

import jakarta.annotation.PreDestroy;
import org.rostilos.codecrow.core.service.JobService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.time.OffsetDateTime;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Maintains a database-backed producer lease for legacy shared-collection RAG
 * updates. Exact-generation jobs use {@code RagIndexOperation} instead.
 */
@Service
public class LegacyRagJobLeaseService {
    private static final Logger log = LoggerFactory.getLogger(
            LegacyRagJobLeaseService.class);

    private final JobService jobService;
    private final ScheduledExecutorService executor;
    private final long leaseSeconds;
    private final long heartbeatIntervalSeconds;

    @Autowired
    public LegacyRagJobLeaseService(
            JobService jobService,
            @Value("${codecrow.rag.legacy-job.lease-seconds:120}") long leaseSeconds,
            @Value("${codecrow.rag.legacy-job.heartbeat-interval-seconds:15}")
            long heartbeatIntervalSeconds,
            @Value("${codecrow.rag.legacy-job.heartbeat-threads:4}")
            int heartbeatThreads) {
        this(
                jobService,
                Executors.newScheduledThreadPool(
                        Math.max(2, heartbeatThreads),
                        new HeartbeatThreadFactory()),
                leaseSeconds,
                heartbeatIntervalSeconds);
    }

    LegacyRagJobLeaseService(
            JobService jobService,
            ScheduledExecutorService executor,
            long leaseSeconds,
            long heartbeatIntervalSeconds) {
        this.jobService = jobService;
        this.executor = executor;
        this.leaseSeconds = Math.max(30, leaseSeconds);
        this.heartbeatIntervalSeconds = Math.max(
                1,
                Math.min(heartbeatIntervalSeconds, this.leaseSeconds / 3));
    }

    /**
     * Start supervising a RUNNING legacy RAG job. The first renewal is
     * synchronous, so callers can refuse remote mutation without proven
     * ownership.
     */
    public JobLease start(long jobId) {
        ActiveJobLease lease = new ActiveJobLease(jobId);
        lease.renew();
        lease.schedule();
        return lease;
    }

    public interface JobLease extends AutoCloseable {
        boolean isOwnershipLost();

        /** Earliest activity timestamp that is still owned by this lease. */
        OffsetDateTime validAfter();

        /** Atomically prove ownership immediately before durable publication. */
        boolean confirmOwnership();

        @Override
        void close();
    }

    private final class ActiveJobLease implements JobLease {
        private final long jobId;
        private final AtomicBoolean ownershipLost = new AtomicBoolean(false);
        private final AtomicBoolean closed = new AtomicBoolean(false);
        private final AtomicBoolean transientFailureReported = new AtomicBoolean(false);
        private final AtomicReference<OffsetDateTime> knownExpiresAt =
                new AtomicReference<>();
        private volatile ScheduledFuture<?> heartbeat;

        private ActiveJobLease(long jobId) {
            this.jobId = jobId;
        }

        private void schedule() {
            if (ownershipLost.get()) {
                return;
            }
            heartbeat = executor.scheduleWithFixedDelay(
                    this::renew,
                    heartbeatIntervalSeconds,
                    heartbeatIntervalSeconds,
                    TimeUnit.SECONDS);
        }

        private boolean renew() {
            if (closed.get() || ownershipLost.get()) {
                return false;
            }
            OffsetDateTime renewalStartedAt = OffsetDateTime.now();
            try {
                boolean renewed = jobService.renewLegacyRagJobLease(
                        jobId,
                        renewalStartedAt.minusSeconds(leaseSeconds),
                        renewalStartedAt);
                if (!renewed) {
                    ownershipLost.set(true);
                    log.info("Legacy RAG job lease ownership was lost: job={}", jobId);
                    return false;
                }
                knownExpiresAt.set(
                        renewalStartedAt.plusSeconds(leaseSeconds).minusSeconds(1));
                if (transientFailureReported.compareAndSet(true, false)) {
                    log.info("Legacy RAG job heartbeat recovered: job={}", jobId);
                }
                return true;
            } catch (RuntimeException renewalFailure) {
                OffsetDateTime knownExpiry = knownExpiresAt.get();
                boolean previousLeaseStillActive = knownExpiry != null
                        && OffsetDateTime.now().isBefore(knownExpiry);
                if (!previousLeaseStillActive) {
                    ownershipLost.set(true);
                    log.info(
                            "Legacy RAG job ownership could not be confirmed before its known expiry: "
                                    + "job={}, detail={}",
                            jobId,
                            renewalFailure.getMessage());
                    return false;
                }
                if (transientFailureReported.compareAndSet(false, true)) {
                    log.warn(
                            "Legacy RAG job heartbeat failed within the active lease; retrying: "
                                    + "job={}, detail={}",
                            jobId,
                            renewalFailure.getMessage());
                } else {
                    log.debug(
                            "Legacy RAG job heartbeat remains degraded within the active lease: job={}",
                            jobId);
                }
                return true;
            }
        }

        @Override
        public boolean isOwnershipLost() {
            return ownershipLost.get();
        }

        @Override
        public OffsetDateTime validAfter() {
            return OffsetDateTime.now().minusSeconds(leaseSeconds);
        }

        @Override
        public boolean confirmOwnership() {
            return renew();
        }

        @Override
        public void close() {
            if (!closed.compareAndSet(false, true)) {
                return;
            }
            ScheduledFuture<?> scheduled = heartbeat;
            if (scheduled != null) {
                scheduled.cancel(false);
            }
        }
    }

    @PreDestroy
    void close() {
        executor.shutdownNow();
    }

    private static final class HeartbeatThreadFactory implements ThreadFactory {
        private final AtomicInteger sequence = new AtomicInteger();

        @Override
        public Thread newThread(Runnable runnable) {
            Thread thread = new Thread(
                    runnable,
                    "legacy-rag-job-heartbeat-" + sequence.incrementAndGet());
            thread.setDaemon(true);
            return thread;
        }
    }
}
