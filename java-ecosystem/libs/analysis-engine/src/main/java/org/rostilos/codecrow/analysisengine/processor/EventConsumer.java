package org.rostilos.codecrow.analysisengine.processor;

import java.util.Map;

/**
 * Common interface for consuming analysis events.
 * Used by processors to stream events to consumers.
 */
@FunctionalInterface
public interface EventConsumer {
    void accept(Map<String, Object> event);
}
