package org.rostilos.codecrow.ragengine.branch;

import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;
import org.springframework.core.env.SystemEnvironmentPropertySource;

import java.time.Duration;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Future;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.awaitility.Awaitility.await;

class BranchIndexBuildExecutorConfigurationTest {

    private final ApplicationContextRunner contextRunner = new ApplicationContextRunner()
            .withUserConfiguration(BranchIndexBuildExecutorConfiguration.class);

    @Test
    void defaultCapacityRunsIndependentRepositoryBuildsConcurrently() {
        contextRunner.run(context -> {
            assertThat(context).hasNotFailed();
            ThreadPoolTaskExecutor executor = context.getBean(
                    "branchIndexBuildExecutor", ThreadPoolTaskExecutor.class);
            assertConcurrentCapacity(executor, 16);
        });
    }

    @Test
    void configuredCapacityRejectsOverflowWithoutAnEphemeralBacklog() {
        contextRunner.withPropertyValues(
                "codecrow.rag.branch-build.global-parallelism=2").run(context -> {
            assertThat(context).hasNotFailed();
            ThreadPoolTaskExecutor executor = context.getBean(
                    "branchIndexBuildExecutor", ThreadPoolTaskExecutor.class);
            assertConcurrentCapacity(executor, 2);
        });
    }

    @Test
    void deploymentEnvironmentVariableControlsTheSameCanonicalProperty() {
        contextRunner.withInitializer(context -> context.getEnvironment()
                .getPropertySources().addFirst(new SystemEnvironmentPropertySource(
                        "deployment-test", Map.of(
                                "CODECROW_RAG_BRANCH_BUILD_GLOBAL_PARALLELISM", "3"))))
                .run(context -> {
                    assertThat(context).hasNotFailed();
                    ThreadPoolTaskExecutor executor = context.getBean(
                            "branchIndexBuildExecutor", ThreadPoolTaskExecutor.class);
                    assertConcurrentCapacity(executor, 3);
                });
    }

    @Test
    void failedBuildReleasesCapacityAndContextShutdownClosesWorkers() {
        ThreadPoolTaskExecutor[] configured = new ThreadPoolTaskExecutor[1];
        contextRunner.withPropertyValues(
                "codecrow.rag.branch-build.global-parallelism=1").run(context -> {
            ThreadPoolTaskExecutor executor = context.getBean(
                    "branchIndexBuildExecutor", ThreadPoolTaskExecutor.class);
            configured[0] = executor;
            Future<?> failedBuild = executor.submit(() -> {
                throw new IllegalStateException("repository download failed");
            });
            assertThatThrownBy(() -> failedBuild.get(2, TimeUnit.SECONDS))
                    .isInstanceOf(ExecutionException.class)
                    .hasCauseInstanceOf(IllegalStateException.class);
            await().atMost(Duration.ofSeconds(2)).until(
                    () -> executor.getActiveCount() == 0);

            CountDownLatch nextBuild = new CountDownLatch(1);
            executor.execute(nextBuild::countDown);
            assertThat(nextBuild.await(2, TimeUnit.SECONDS)).isTrue();
            assertThat(executor.getThreadPoolExecutor().getQueue()).isEmpty();
        });

        assertThat(configured[0].getThreadPoolExecutor().isTerminated()).isTrue();
    }

    private static void assertConcurrentCapacity(
            ThreadPoolTaskExecutor executor, int workers) throws Exception {
        CountDownLatch started = new CountDownLatch(workers);
        CountDownLatch release = new CountDownLatch(1);
        AtomicBoolean rejectedWorkRan = new AtomicBoolean(false);
        try {
            for (int index = 0; index < workers; index++) {
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
            assertThat(executor.getCorePoolSize()).isEqualTo(workers);
            assertThat(executor.getMaxPoolSize()).isEqualTo(workers);
            assertThatThrownBy(() -> executor.execute(
                    () -> rejectedWorkRan.set(true)))
                    .isInstanceOf(RejectedExecutionException.class);
            assertThat(executor.getThreadPoolExecutor().getQueue()).isEmpty();
        } finally {
            release.countDown();
        }
        await().atMost(Duration.ofSeconds(2)).until(
                () -> executor.getActiveCount() == 0);
        assertThat(rejectedWorkRan).isFalse();
    }
}
