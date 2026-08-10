package org.rostilos.codecrow.pipelineagent.config;

import org.junit.jupiter.api.Test;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executor;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;

class AsyncConfigTest {

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
