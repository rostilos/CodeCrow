package org.rostilos.codecrow.pipelineagent.config;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.pipelineagent.generic.config.WebMvcConfig;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;
import org.springframework.core.task.AsyncTaskExecutor;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.web.servlet.config.annotation.AsyncSupportConfigurer;

import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class ReviewEntryConcurrencyTest {
    @Test
    void sixteenReviewActionsAndIndexesRunWhileAllThirtyTwoStreamsWaitForThem() throws Exception {
        try (var context = context(Map.of())) {
            ThreadPoolTaskExecutor actions = context.getBean("pipelineActionExecutor", ThreadPoolTaskExecutor.class);
            ThreadPoolTaskExecutor indexes = context.getBean("ragExecutor", ThreadPoolTaskExecutor.class);
            ThreadPoolTaskExecutor streams = context.getBean("webMvcAsyncExecutor", ThreadPoolTaskExecutor.class);
            CapturedConfigurer configurer = new CapturedConfigurer();
            context.getBean(WebMvcConfig.class).configureAsyncSupport(configurer);
            CountDownLatch actionsStarted = new CountDownLatch(16);
            CountDownLatch indexesStarted = new CountDownLatch(16);
            CountDownLatch streamsStarted = new CountDownLatch(32);
            CountDownLatch release = new CountDownLatch(1);
            try {
                for (int index = 0; index < 16; index++) {
                    CompletableFuture<Void> action = CompletableFuture.runAsync(
                            () -> block(actionsStarted, release), actions);
                    CompletableFuture<Void> maintenance = CompletableFuture.runAsync(
                            () -> block(indexesStarted, release), indexes);
                    configurer.executor().execute(() -> { streamsStarted.countDown(); action.join(); });
                    configurer.executor().execute(() -> { streamsStarted.countDown(); maintenance.join(); });
                }
                assertThat(actionsStarted.await(3, TimeUnit.SECONDS)).isTrue();
                assertThat(indexesStarted.await(3, TimeUnit.SECONDS)).isTrue();
                assertThat(streamsStarted.await(3, TimeUnit.SECONDS)).isTrue();
                assertThat(actions.getActiveCount()).isEqualTo(16);
                assertThat(indexes.getActiveCount()).isEqualTo(16);
                assertThat(streams.getActiveCount()).isEqualTo(32);
                assertThat(configurer.timeout()).isEqualTo(-1L);
            } finally {
                release.countDown();
            }
        }
    }

    @Test
    void saturatedReviewIndexAndStreamPoolsQueueTheNextAcceptedRequest() throws Exception {
        try (var context = context(Map.of())) {
            for (String bean : java.util.List.of("pipelineActionExecutor", "ragExecutor", "webMvcAsyncExecutor")) {
                ThreadPoolTaskExecutor executor = context.getBean(bean, ThreadPoolTaskExecutor.class);
                int concurrency = executor.getCorePoolSize();
                CountDownLatch started = new CountDownLatch(concurrency);
                CountDownLatch release = new CountDownLatch(1);
                CompletableFuture<Void> waiting;
                try {
                    for (int index = 0; index < concurrency; index++) {
                        executor.execute(() -> block(started, release));
                    }
                    assertThat(started.await(3, TimeUnit.SECONDS)).isTrue();
                    waiting = CompletableFuture.runAsync(() -> {}, executor);
                    assertThat(waiting).isNotDone();
                    assertThat(executor.getThreadPoolExecutor().getQueue()).hasSize(1);
                } finally {
                    release.countDown();
                }
                waiting.get(3, TimeUnit.SECONDS);
            }
        }
    }

    @Test
    void explicitEntryCapacitiesTimeoutAndSecurityContextArePreserved() throws Exception {
        try (var context = context(Map.of(
                "pipeline.review.concurrency", "12",
                "codecrow.rag.branch-build.global-parallelism", "10",
                "pipeline.streaming.concurrency", "24",
                "spring.mvc.async.request-timeout", "15m"))) {
            assertThat(context.getBean("pipelineActionExecutor", ThreadPoolTaskExecutor.class).getCorePoolSize()).isEqualTo(12);
            assertThat(context.getBean("ragExecutor", ThreadPoolTaskExecutor.class).getCorePoolSize()).isEqualTo(10);
            assertThat(context.getBean("webMvcAsyncExecutor", ThreadPoolTaskExecutor.class).getCorePoolSize()).isEqualTo(24);
            CapturedConfigurer configurer = new CapturedConfigurer();
            context.getBean(WebMvcConfig.class).configureAsyncSupport(configurer);
            assertThat(configurer.timeout()).isEqualTo(900000L);
            SecurityContextHolder.getContext().setAuthentication(
                    new UsernamePasswordAuthenticationToken("project-principal", "unused"));
            try {
                assertThat(configurer.executor().submit(
                        () -> SecurityContextHolder.getContext().getAuthentication().getName())
                        .get(3, TimeUnit.SECONDS)).isEqualTo("project-principal");
            } finally {
                SecurityContextHolder.clearContext();
            }
        }
    }

    @Test
    void webhookCapacityAlreadyExpandsBeyondCoreAndRetainsDurableSaturationPolicy() throws Exception {
        ThreadPoolTaskExecutor executor = (ThreadPoolTaskExecutor) new AsyncConfig().webhookExecutor(8, 20);
        CountDownLatch started = new CountDownLatch(20);
        CountDownLatch release = new CountDownLatch(1);
        try {
            for (int index = 0; index < 20; index++) {
                executor.execute(() -> block(started, release));
            }
            assertThat(started.await(3, TimeUnit.SECONDS)).isTrue();
            assertThat(executor.getQueueCapacity()).isZero();
            assertThatThrownBy(() -> executor.execute(() -> {}))
                    .isInstanceOf(org.springframework.core.task.TaskRejectedException.class);
            assertThat(executor.getThreadPoolExecutor().getQueue()).isEmpty();
        } finally {
            release.countDown();
            executor.shutdown();
        }
    }

    private static AnnotationConfigApplicationContext context(Map<String, Object> properties) {
        var context = new AnnotationConfigApplicationContext();
        context.getEnvironment().getPropertySources().addFirst(
                new org.springframework.core.env.MapPropertySource("test", properties));
        context.register(AsyncConfig.class, WebMvcConfig.class);
        context.refresh();
        return context;
    }

    private static void block(CountDownLatch started, CountDownLatch release) {
        started.countDown();
        try {
            release.await();
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
        }
    }

    private static class CapturedConfigurer extends AsyncSupportConfigurer {
        AsyncTaskExecutor executor() { return getTaskExecutor(); }
        Long timeout() { return getTimeout(); }
    }
}
