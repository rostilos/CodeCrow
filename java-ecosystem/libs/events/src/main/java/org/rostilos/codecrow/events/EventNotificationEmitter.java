package org.rostilos.codecrow.events;

import java.util.Map;
import java.util.function.Consumer;

public class EventNotificationEmitter {
    public static void emitStatus(Consumer<Map<String, Object>> consumer, String state, String description) {
        consumer.accept(
            Map.of(
                    "type", "status",
                    "state", state,
                    "message", description));
    }
}
