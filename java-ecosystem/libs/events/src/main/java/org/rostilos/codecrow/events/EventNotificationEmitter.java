package org.rostilos.codecrow.events;

import java.util.Map;
import java.util.function.Consumer;

public class EventNotificationEmitter {
    private EventNotificationEmitter() {
    }

    /**
     * Delivers an observational progress event. A missing or disconnected
     * observer must never change the durable analysis outcome.
     */
    public static void emitStatus(Consumer<Map<String, Object>> consumer, String state, String description) {
        if (consumer == null) {
            return;
        }
        try {
            consumer.accept(Map.of(
                    "type", "status",
                    "state", state,
                    "message", description));
        } catch (RuntimeException ignored) {
            // Observers are optional transport adapters. Their lifecycle is not
            // part of the durable operation result.
        }
    }
}
