package org.rostilos.codecrow.testsupport.offline;

import org.junit.jupiter.api.Test;

import java.time.Duration;
import java.time.Instant;
import java.time.ZoneId;
import java.time.ZoneOffset;

import static org.assertj.core.api.Assertions.assertThat;

class DeterministicPrimitivesTest {

    @Test
    void advancesTimeExplicitlyAndPreservesTheRequestedZone() {
        MutableTestClock clock = new MutableTestClock(
                Instant.parse("2026-07-14T10:00:00Z"),
                ZoneOffset.UTC
        );

        clock.advance(Duration.ofSeconds(7));
        MutableTestClock kyivClock = clock.withZone(ZoneId.of("Europe/Kyiv"));

        assertThat(clock.instant()).isEqualTo(Instant.parse("2026-07-14T10:00:07Z"));
        assertThat(clock.getZone()).isEqualTo(ZoneOffset.UTC);
        assertThat(kyivClock.instant()).isEqualTo(clock.instant());
        assertThat(kyivClock.getZone()).isEqualTo(ZoneId.of("Europe/Kyiv"));

        kyivClock.advance(Duration.ofSeconds(-2));
        assertThat(kyivClock.millis()).isEqualTo(Instant.parse("2026-07-14T10:00:05Z").toEpochMilli());
        assertThat(clock.instant()).isEqualTo(Instant.parse("2026-07-14T10:00:07Z"));
    }

    @Test
    void emitsStableMonotonicIds() {
        DeterministicIds ids = new DeterministicIds("execution", 40);

        assertThat(ids.next()).isEqualTo("execution-40");
        assertThat(ids.next()).isEqualTo("execution-41");
    }
}
