package org.rostilos.codecrow.events;

import org.springframework.context.ApplicationEvent;

import java.time.Instant;
import java.util.UUID;

/**
 * Base class for all Codecrow events.
 * Provides common functionality for event tracking and correlation.
 */
public abstract class CodecrowEvent extends ApplicationEvent {
    
    private final String eventId;
    private final Instant timestamp;
    private final String correlationId;
    
    protected CodecrowEvent(Object source) {
        this(source, null);
    }
    
    protected CodecrowEvent(Object source, String correlationId) {
        super(source);
        this.eventId = UUID.randomUUID().toString();
        this.timestamp = Instant.now();
        this.correlationId = correlationId != null ? correlationId : this.eventId;
    }
    
    public String getEventId() {
        return eventId;
    }
    
    public Instant getEventTimestamp() {
        return timestamp;
    }
    
    public String getCorrelationId() {
        return correlationId;
    }
    
    public abstract String getEventType();
}
