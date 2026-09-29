package org.rostilos.codecrow.pipelineagent.config;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;
import org.springframework.scheduling.concurrent.ThreadPoolTaskScheduler;

import java.util.concurrent.Executor;

/**
 * Configuration for async task execution with dedicated thread pools.
 * 
 * Provides separate executors for different types of async operations:
 * - webhookExecutor: For webhook processing (mixed workload)
 * - ragExecutor: For RAG indexing operations (I/O bound)
 * - emailExecutor: For email sending (fire-and-forget)
 */
@Configuration
public class AsyncConfig {

    private static final Logger log = LoggerFactory.getLogger(AsyncConfig.class);

    /**
     * Keep liveness/recovery schedules independent from optional maintenance.
     * A slow provider or repository-index task must not prevent durable job
     * recovery, heartbeat checks, or queue reconciliation from running.
     */
    @Bean(name = "taskScheduler")
    public ThreadPoolTaskScheduler taskScheduler(
            @Value("${spring.task.scheduling.pool.size:4}") int poolSize) {
        ThreadPoolTaskScheduler scheduler = new ThreadPoolTaskScheduler();
        scheduler.setPoolSize(poolSize);
        scheduler.setThreadNamePrefix("scheduling-");
        scheduler.setWaitForTasksToCompleteOnShutdown(true);
        scheduler.setAwaitTerminationSeconds(30);
        log.info("Scheduled task executor initialized with pool={}", poolSize);
        return scheduler;
    }

    /**
     * Default executor for general async tasks.
     */
    @Bean(name = "taskExecutor")
    public Executor taskExecutor() {
        ThreadPoolTaskExecutor executor = new ThreadPoolTaskExecutor();
        executor.setCorePoolSize(4);
        executor.setMaxPoolSize(10);
        executor.setQueueCapacity(100);
        executor.setThreadNamePrefix("async-default-");
        executor.setWaitForTasksToCompleteOnShutdown(true);
        executor.setAwaitTerminationSeconds(60);
        executor.initialize();
        return executor;
    }

    /**
     * Dedicated executor for webhook processing.
     * Sized for concurrent webhook handling from VCS providers.
     */
    @Bean(name = "webhookExecutor")
    public Executor webhookExecutor(
            @Value("${webhook.executor.core-pool-size:8}") int corePoolSize,
            @Value("${webhook.executor.max-pool-size:20}") int maxPoolSize) {
        ThreadPoolTaskExecutor executor = new ThreadPoolTaskExecutor();
        executor.setCorePoolSize(corePoolSize);
        executor.setMaxPoolSize(maxPoolSize);
        // Do not accept work into an ephemeral in-memory backlog. Saturated work
        // remains QUEUED in the database and is retried by the recovery scheduler.
        executor.setQueueCapacity(0);
        executor.setThreadNamePrefix("webhook-");
        executor.setWaitForTasksToCompleteOnShutdown(true);
        executor.setAwaitTerminationSeconds(120);
        executor.setRejectedExecutionHandler(new java.util.concurrent.ThreadPoolExecutor.AbortPolicy());
        executor.initialize();
        log.info("Webhook executor initialized with core={}, max={}, durable database backlog enabled",
                corePoolSize, maxPoolSize);
        return executor;
    }

    /** Blocking manual PR/branch actions must not occupy the common ForkJoin pool. */
    @Bean(name = "pipelineActionExecutor")
    public ThreadPoolTaskExecutor pipelineActionExecutor(
            @Value("${pipeline.review.concurrency:16}") int concurrency) {
        return blockingExecutor(concurrency, "pipeline-action-", 120);
    }

    /**
     * Stream writers wait for work performed by the action/index executors.
     * Keeping these pools separate avoids all writers waiting for work queued
     * behind themselves. The default accommodates both classes of stream.
     */
    @Bean(name = "webMvcAsyncExecutor")
    public ThreadPoolTaskExecutor webMvcAsyncExecutor(
            @Value("${pipeline.review.concurrency:16}") int reviews,
            @Value("${codecrow.rag.branch-build.global-parallelism:16}") int indexes,
            @Value("${pipeline.streaming.concurrency:0}") int configured) {
        return blockingExecutor(configured > 0 ? configured : reviews + indexes,
                "mvc-async-", 120);
    }

    /**
     * Manual index maintenance is blocking I/O orchestration. It must use a
     * different pool from both review actions and branch-build dispatch itself.
     */
    @Bean(name = "ragExecutor")
    public ThreadPoolTaskExecutor ragExecutor(
            @Value("${codecrow.rag.branch-build.global-parallelism:16}") int concurrency) {
        return blockingExecutor(concurrency, "rag-maintenance-", 300);
    }

    private static ThreadPoolTaskExecutor blockingExecutor(
            int concurrency, String prefix, int shutdownSeconds) {
        ThreadPoolTaskExecutor executor = new ThreadPoolTaskExecutor();
        executor.setCorePoolSize(concurrency);
        executor.setMaxPoolSize(concurrency);
        // Preserve accepted request work when the I/O workers are occupied. These
        // request/stream submissions have no webhook-style durable redelivery
        // at the executor boundary; saturation must not reject the next request.
        // Running work is bounded by the fixed pool, waiting work stays queued.
        executor.setQueueCapacity(Integer.MAX_VALUE);
        executor.setThreadNamePrefix(prefix);
        executor.setWaitForTasksToCompleteOnShutdown(true);
        executor.setAwaitTerminationSeconds(shutdownSeconds);
        executor.setRejectedExecutionHandler(new java.util.concurrent.ThreadPoolExecutor.AbortPolicy());
        executor.initialize();
        log.info("Executor initialized: name={} concurrency={} waiting queue enabled",
                prefix, concurrency);
        return executor;
    }

    /**
     * Dedicated executor for email sending.
     * Low core size since emails are quick but may have network latency.
     */
    @Bean(name = "emailExecutor")
    public Executor emailExecutor() {
        ThreadPoolTaskExecutor executor = new ThreadPoolTaskExecutor();
        executor.setCorePoolSize(2);
        executor.setMaxPoolSize(5);
        executor.setQueueCapacity(200);
        executor.setThreadNamePrefix("email-");
        executor.setWaitForTasksToCompleteOnShutdown(true);
        executor.setAwaitTerminationSeconds(30);
        executor.initialize();
        log.info("Email executor initialized with core={}, max={}", 2, 5);
        return executor;
    }
}
