package org.rostilos.codecrow.ragengine.branch;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;

import java.util.concurrent.Executor;

/**
 * I/O orchestration capacity for independent configured branch snapshots.
 * Heavy structural-index work has separate admission in the Python RAG service.
 */
@Configuration
public class BranchIndexBuildExecutorConfiguration {
    private static final Logger log = LoggerFactory.getLogger(
            BranchIndexBuildExecutorConfiguration.class);

    @Bean(name = "branchIndexBuildExecutor")
    public Executor branchIndexBuildExecutor(
            @Value("${codecrow.rag.branch-build.global-parallelism:16}") int parallelism) {
        int workers = Math.max(1, parallelism);
        ThreadPoolTaskExecutor executor = new ThreadPoolTaskExecutor();
        executor.setCorePoolSize(workers);
        executor.setMaxPoolSize(workers);
        // The database Job row is the backlog. Rejection returns ownership to
        // PENDING instead of accepting work only in process memory.
        executor.setQueueCapacity(0);
        executor.setThreadNamePrefix("rag-branch-build-");
        executor.setWaitForTasksToCompleteOnShutdown(true);
        executor.setAwaitTerminationSeconds(300);
        executor.setRejectedExecutionHandler(
                new java.util.concurrent.ThreadPoolExecutor.AbortPolicy());
        executor.initialize();
        log.info(
                "Repository-index dispatch initialized with workers={} "
                        + "(codecrow.rag.branch-build.global-parallelism); "
                        + "pending work remains in the durable database queue. "
                        + "Python structural-index capacity is configured separately "
                        + "by RAG_FULL_INDEX_CONCURRENCY",
                workers);
        return executor;
    }
}
