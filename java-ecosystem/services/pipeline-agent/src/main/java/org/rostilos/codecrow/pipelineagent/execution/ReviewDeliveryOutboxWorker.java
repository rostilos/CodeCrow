package org.rostilos.codecrow.pipelineagent.execution;

import org.rostilos.codecrow.analysisengine.delivery.ReviewDeliveryOutboxPort;
import org.rostilos.codecrow.analysisengine.delivery.ReviewDeliveryService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;

import java.time.Clock;
import java.time.Instant;
import java.util.Objects;

/** Bounded recovery loop; it consumes outbox state and never reruns analysis. */
public final class ReviewDeliveryOutboxWorker {
    private static final Logger log =
            LoggerFactory.getLogger(ReviewDeliveryOutboxWorker.class);

    private final ReviewDeliveryOutboxPort outbox;
    private final ReviewDeliveryService delivery;
    private final Clock clock;
    private final int batchSize;

    public ReviewDeliveryOutboxWorker(
            ReviewDeliveryOutboxPort outbox,
            ReviewDeliveryService delivery,
            Clock clock,
            int batchSize) {
        this.outbox = Objects.requireNonNull(outbox, "outbox");
        this.delivery = Objects.requireNonNull(delivery, "delivery");
        this.clock = Objects.requireNonNull(clock, "clock");
        if (batchSize < 1 || batchSize > 1_000) {
            throw new IllegalArgumentException("delivery batchSize must be 1..1000");
        }
        this.batchSize = batchSize;
    }

    @Scheduled(
            initialDelayString = "${codecrow.review.delivery.initial-delay-ms:60000}",
            fixedDelayString = "${codecrow.review.delivery.poll-delay-ms:15000}")
    public int retryDue() {
        Instant now = clock.instant();
        int attempted = 0;
        for (String intentId : outbox.findDueIntentIds(now, batchSize)) {
            try {
                delivery.attempt(intentId, now);
                attempted++;
            } catch (RuntimeException error) {
                log.warn("Delivery recovery failed closed for intent {}: {}",
                        intentId, error.getClass().getSimpleName());
            }
        }
        return attempted;
    }
}
