package org.rostilos.codecrow.events;

import org.junit.jupiter.api.Test;

import java.time.Instant;

import static org.assertj.core.api.Assertions.assertThat;

class CodecrowEventTest {

    private static class TestCodecrowEvent extends CodecrowEvent {
        protected TestCodecrowEvent(Object source) {
            super(source);
        }

        protected TestCodecrowEvent(Object source, String correlationId) {
            super(source, correlationId);
        }

        @Override
        public String getEventType() {
            return "TEST_EVENT";
        }
    }

    @Test
    void testEventCreation_WithoutCorrelationId() {
        Object source = new Object();

        TestCodecrowEvent event = new TestCodecrowEvent(source);

        assertThat(event.getSource()).isEqualTo(source);
        assertThat(event.getEventId()).isNotNull();
        assertThat(event.getEventTimestamp()).isNotNull();
        assertThat(event.getCorrelationId()).isEqualTo(event.getEventId());
        assertThat(event.getEventType()).isEqualTo("TEST_EVENT");
    }

    @Test
    void testEventCreation_WithCorrelationId() {
        Object source = new Object();
        String correlationId = "custom-correlation-id";

        TestCodecrowEvent event = new TestCodecrowEvent(source, correlationId);

        assertThat(event.getCorrelationId()).isEqualTo(correlationId);
        assertThat(event.getEventId()).isNotEqualTo(correlationId);
    }

    @Test
    void testEventTimestamp_RecentTime() {
        Instant before = Instant.now();
        TestCodecrowEvent event = new TestCodecrowEvent(this);
        Instant after = Instant.now();

        assertThat(event.getEventTimestamp())
                .isAfterOrEqualTo(before)
                .isBeforeOrEqualTo(after);
    }

    @Test
    void testMultipleEvents_UniqueIds() {
        TestCodecrowEvent event1 = new TestCodecrowEvent(this);
        TestCodecrowEvent event2 = new TestCodecrowEvent(this);

        assertThat(event1.getEventId()).isNotEqualTo(event2.getEventId());
    }

    @Test
    void testEventId_IsUUID() {
        TestCodecrowEvent event = new TestCodecrowEvent(this);

        assertThat(event.getEventId()).matches(
                "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
        );
    }
}
