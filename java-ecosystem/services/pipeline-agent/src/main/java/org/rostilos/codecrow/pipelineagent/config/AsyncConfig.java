package org.rostilos.codecrow.pipelineagent.config;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;

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
    public Executor webhookExecutor() {
        ThreadPoolTaskExecutor executor = new ThreadPoolTaskExecutor();
        executor.setCorePoolSize(4);
        executor.setMaxPoolSize(8);
        executor.setQueueCapacity(100);
        executor.setThreadNamePrefix("webhook-");
        executor.setWaitForTasksToCompleteOnShutdown(true);
        executor.setAwaitTerminationSeconds(120);
        // Log when tasks are rejected due to full queue
        executor.setRejectedExecutionHandler((r, e) -> {
            log.error("WEBHOOK EXECUTOR REJECTED TASK! Queue is full. Pool size: {}, Active: {}, Queue size: {}",
                    e.getPoolSize(), e.getActiveCount(), e.getQueue().size());
            // Try to run in caller thread as fallback
            if (!e.isShutdown()) {
                r.run();
            }
        });
        executor.initialize();
        log.info("Webhook executor initialized with core={}, max={}, queueCapacity={}", 4, 8, 100);
        return executor;
    }

    /**
     * Dedicated executor for RAG indexing operations.
     * Lower concurrency since RAG operations are resource-intensive.
     */
    @Bean(name = "ragExecutor")
    public Executor ragExecutor() {
        ThreadPoolTaskExecutor executor = new ThreadPoolTaskExecutor();
        executor.setCorePoolSize(2);
        executor.setMaxPoolSize(4);
        executor.setQueueCapacity(50);
        executor.setThreadNamePrefix("rag-");
        executor.setWaitForTasksToCompleteOnShutdown(true);
        executor.setAwaitTerminationSeconds(300); // RAG operations can be long
        executor.initialize();
        log.info("RAG executor initialized with core={}, max={}", 2, 4);
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
