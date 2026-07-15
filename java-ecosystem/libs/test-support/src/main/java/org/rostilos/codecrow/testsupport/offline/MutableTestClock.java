package org.rostilos.codecrow.testsupport.offline;

import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneId;
import java.util.Objects;
import java.util.concurrent.atomic.AtomicReference;

/** Clock whose time changes only through explicit test actions. */
public final class MutableTestClock extends Clock {

    private final AtomicReference<Instant> current;
    private final ZoneId zone;

    public MutableTestClock(Instant initialInstant, ZoneId zone) {
        this.current = new AtomicReference<>(Objects.requireNonNull(initialInstant, "initialInstant"));
        this.zone = Objects.requireNonNull(zone, "zone");
    }

    public void advance(Duration duration) {
        Objects.requireNonNull(duration, "duration");
        current.updateAndGet(instant -> instant.plus(duration));
    }

    @Override
    public ZoneId getZone() {
        return zone;
    }

    @Override
    public MutableTestClock withZone(ZoneId requestedZone) {
        return new MutableTestClock(current.get(), requestedZone);
    }

    @Override
    public Instant instant() {
        return current.get();
    }
}
