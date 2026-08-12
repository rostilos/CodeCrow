package org.rostilos.codecrow.pipelineagent.config;

import org.junit.jupiter.api.Test;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;
import org.springframework.scheduling.concurrent.ThreadPoolTaskScheduler;

import java.time.Instant;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executor;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;

class AsyncConfigTest {

    @Test
    void scheduledRecoveryIsNotStarvedBySlowOptionalMaintenance() throws Exception {
        ThreadPoolTaskScheduler scheduler = new AsyncConfig().taskScheduler(2);
        scheduler.initialize();
        CountDownLatch started = new CountDownLatch(2);
        CountDownLatch release = new CountDownLatch(1);

        try {
            Runnable blockingTask = () -> {
                started.countDown();
                try {
                    release.await();
                } catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                }
            };
            scheduler.schedule(blockingTask, Instant.now());
            scheduler.schedule(blockingTask, Instant.now());

            assertThat(started.await(2, TimeUnit.SECONDS)).isTrue();
            assertThat(scheduler.getPoolSize()).isEqualTo(2);
        } finally {
            release.countDown();
            scheduler.shutdown();
        }
    }

    @Test
    void webhookExecutorRunsFiveAcceptedReviewsInParallel() throws Exception {
        Executor configured = new AsyncConfig().webhookExecutor(2, 5);
        ThreadPoolTaskExecutor executor = (ThreadPoolTaskExecutor) configured;
        CountDownLatch started = new CountDownLatch(5);
        CountDownLatch release = new CountDownLatch(1);

        try {
            for (int i = 0; i < 5; i++) {
                executor.execute(() -> {
                    started.countDown();
                    try {
                        release.await();
                    } catch (InterruptedException interrupted) {
                        Thread.currentThread().interrupt();
                    }
                });
            }

            assertThat(started.await(2, TimeUnit.SECONDS)).isTrue();
            assertThat(executor.getMaxPoolSize()).isEqualTo(5);
        } finally {
            release.countDown();
            executor.shutdown();
        }
    }
}
