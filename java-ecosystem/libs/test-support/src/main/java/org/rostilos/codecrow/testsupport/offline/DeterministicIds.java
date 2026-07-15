package org.rostilos.codecrow.testsupport.offline;

import java.util.Objects;
import java.util.concurrent.atomic.AtomicLong;

/** Monotonic, reproducible ID source for fixtures and controlled schedules. */
public final class DeterministicIds {

    private final String prefix;
    private final AtomicLong nextValue;

    public DeterministicIds(String prefix, long initialValue) {
        this.prefix = Objects.requireNonNull(prefix, "prefix");
        this.nextValue = new AtomicLong(initialValue);
    }

    public String next() {
        return prefix + "-" + nextValue.getAndIncrement();
    }
}
