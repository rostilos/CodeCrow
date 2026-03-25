package org.rostilos.codecrow.core.util;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;

/**
 * Shared utility for executing operations with exponential backoff retry.
 * <p>
 * Extracted from duplicated retry patterns across the codebase (VCS client
 * file-existence checks, batch file fetching, event publishing, etc.).
 * </p>
 *
 * <h3>Usage:</h3>
 * <pre>{@code
 *   String result = RetryExecutor.withExponentialBackoff(() -> {
 *       // operation that may throw IOException
 *       return someHttpCall();
 *   });
 *
 *   // With custom retry config:
 *   RetryExecutor.withExponentialBackoff(5, 1000L, () -> {
 *       someVoidOperation();
 *       return null;
 *   });
 * }</pre>
 */
public final class RetryExecutor {

    private static final Logger log = LoggerFactory.getLogger(RetryExecutor.class);

    /** Default maximum number of attempts (initial + retries). */
    public static final int DEFAULT_MAX_RETRIES = 3;

    /** Default initial backoff delay in milliseconds. */
    public static final long DEFAULT_INITIAL_BACKOFF_MS = 2_000;

    private RetryExecutor() {
        // Utility class
    }

    /**
     * A task that may throw {@link IOException} or {@link RuntimeException}.
     *
     * @param <T> the return type
     */
    @FunctionalInterface
    public interface RetryableTask<T> {
        T execute() throws IOException;
    }

    /**
     * Execute a task with default retry parameters (3 attempts, 2s initial backoff).
     *
     * @param task the operation to execute
     * @param <T>  the return type
     * @return the result of the task
     * @throws IOException if all retries are exhausted
     */
    public static <T> T withExponentialBackoff(RetryableTask<T> task) throws IOException {
        return withExponentialBackoff(DEFAULT_MAX_RETRIES, DEFAULT_INITIAL_BACKOFF_MS, task);
    }

    /**
     * Execute a task with configurable retry parameters.
     * <p>
     * Retries on {@link IOException} (including 429 rate limits wrapped as IOException)
     * with exponential backoff (delay doubles each attempt). {@link RuntimeException}s
     * are <strong>not</strong> retried and propagate immediately.
     * </p>
     *
     * @param maxRetries       maximum number of attempts (must be ≥ 1)
     * @param initialBackoffMs initial delay in ms before first retry (doubles each time)
     * @param task             the operation to execute
     * @param <T>              the return type
     * @return the result of the task
     * @throws IOException if all retries are exhausted
     */
    public static <T> T withExponentialBackoff(int maxRetries, long initialBackoffMs,
                                                RetryableTask<T> task) throws IOException {
        if (maxRetries < 1) {
            throw new IllegalArgumentException("maxRetries must be >= 1, got " + maxRetries);
        }

        IOException lastException = null;
        long backoffMs = initialBackoffMs;

        for (int attempt = 1; attempt <= maxRetries; attempt++) {
            try {
                return task.execute();
            } catch (IOException e) {
                lastException = e;
                if (attempt < maxRetries) {
                    log.warn("Attempt {}/{} failed ({}), retrying in {}ms...",
                            attempt, maxRetries, e.getMessage(), backoffMs);
                    sleep(backoffMs);
                    backoffMs *= 2; // Exponential backoff
                } else {
                    log.error("All {} attempts exhausted. Last error: {}", maxRetries, e.getMessage());
                }
            }
        }

        throw lastException != null
                ? lastException
                : new IOException("RetryExecutor: all " + maxRetries + " attempts failed");
    }

    private static void sleep(long ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException ie) {
            Thread.currentThread().interrupt();
            log.warn("Retry sleep interrupted");
        }
    }
}
