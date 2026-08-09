package org.rostilos.codecrow.ragengine.branch;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;

import java.util.concurrent.Executor;

/** Dedicated service capacity for independent configured branch snapshots. */
@Configuration
public class BranchIndexBuildExecutorConfiguration {

    @Bean(name = "branchIndexBuildExecutor")
    public Executor branchIndexBuildExecutor(
            @Value("${codecrow.rag.branch-build.global-parallelism:4}") int parallelism) {
        int workers = Math.max(1, parallelism);
        ThreadPoolTaskExecutor executor = new ThreadPoolTaskExecutor();
        executor.setCorePoolSize(workers);
        executor.setMaxPoolSize(workers);
        executor.setQueueCapacity(50);
        executor.setThreadNamePrefix("rag-branch-build-");
        executor.setWaitForTasksToCompleteOnShutdown(true);
        executor.setAwaitTerminationSeconds(300);
        executor.initialize();
        return executor;
    }
}
