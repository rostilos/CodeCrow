package org.rostilos.codecrow.ragengine.branch;

import jakarta.annotation.PreDestroy;
import org.rostilos.codecrow.ragengine.service.RagBranchIndexRegistryService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;

/** Keeps a live exact-generation operation owned while remote work is running. */
@Service
public class RagIndexOperationHeartbeatService {
    private static final Logger log = LoggerFactory.getLogger(
            RagIndexOperationHeartbeatService.class);
    private static final long HEARTBEAT_INTERVAL_SECONDS = 15;
    private static final int HEARTBEAT_THREADS = 4;

    private final RagBranchIndexRegistryService registryService;
    private final ScheduledExecutorService executor;
    private final long heartbeatIntervalSeconds;

    @Autowired
    public RagIndexOperationHeartbeatService(
            RagBranchIndexRegistryService registryService) {
        this(registryService,
                Executors.newScheduledThreadPool(
                        HEARTBEAT_THREADS, new HeartbeatThreadFactory()),
                HEARTBEAT_INTERVAL_SECONDS);
    }

    RagIndexOperationHeartbeatService(
            RagBranchIndexRegistryService registryService,
            ScheduledExecutorService executor,
            long heartbeatIntervalSeconds) {
        this.registryService = registryService;
        this.executor = executor;
        this.heartbeatIntervalSeconds = heartbeatIntervalSeconds;
    }

    public HeartbeatScope start(long operationId) {
        AtomicBoolean degraded = new AtomicBoolean(false);
        ScheduledFuture<?> heartbeat = executor.scheduleAtFixedRate(
                () -> heartbeat(operationId, degraded),
                heartbeatIntervalSeconds,
                heartbeatIntervalSeconds,
                TimeUnit.SECONDS);
        return () -> heartbeat.cancel(false);
    }

    private void heartbeat(long operationId, AtomicBoolean degraded) {
        try {
            registryService.heartbeatBuild(operationId);
            if (degraded.getAndSet(false)) {
                log.info("RAG generation heartbeat recovered: operation={}", operationId);
            }
        } catch (Exception failure) {
            // A later heartbeat may still succeed. If the producer stops,
            // durable operation recovery owns the terminal transition.
            if (degraded.compareAndSet(false, true)) {
                log.warn(
                        "RAG generation heartbeat failed; retrying: operation={} detail={}",
                        operationId, failure.getMessage());
            } else {
                log.debug(
                        "RAG generation heartbeat remains degraded: operation={} detail={}",
                        operationId, failure.getMessage());
            }
        }
    }

    @PreDestroy
    void close() {
        executor.shutdownNow();
    }

    @FunctionalInterface
    public interface HeartbeatScope extends AutoCloseable {
        @Override
        void close();
    }

    private static final class HeartbeatThreadFactory implements ThreadFactory {
        private final AtomicInteger sequence = new AtomicInteger();

        @Override
        public Thread newThread(Runnable runnable) {
            Thread thread = new Thread(
                    runnable,
                    "rag-generation-heartbeat-" + sequence.incrementAndGet());
            thread.setDaemon(true);
            return thread;
        }
    }
}
