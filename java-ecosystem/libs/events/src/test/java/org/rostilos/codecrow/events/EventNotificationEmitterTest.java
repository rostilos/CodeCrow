package org.rostilos.codecrow.events;

import org.junit.jupiter.api.Test;

import java.util.Map;
import java.util.function.Consumer;

import static org.assertj.core.api.Assertions.assertThatCode;

class EventNotificationEmitterTest {

    @Test
    void missingObserverIsANoOp() {
        assertThatCode(() -> EventNotificationEmitter.emitStatus(null, "started", "Started"))
                .doesNotThrowAnyException();
    }

    @Test
    void observerFailureCannotChangeOperationOutcome() {
        Consumer<Map<String, Object>> disconnected = event -> {
            throw new IllegalStateException("stream disconnected");
        };

        assertThatCode(() -> EventNotificationEmitter.emitStatus(
                disconnected, "completed", "Completed"))
                .doesNotThrowAnyException();
    }
}
