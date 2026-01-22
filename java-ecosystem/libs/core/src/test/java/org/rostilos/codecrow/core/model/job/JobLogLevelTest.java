package org.rostilos.codecrow.core.model.job;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("JobLogLevel")
class JobLogLevelTest {

    @Test
    @DisplayName("should have all expected values")
    void shouldHaveAllExpectedValues() {
        JobLogLevel[] values = JobLogLevel.values();
        
        assertThat(values).hasSize(4);
        assertThat(values).contains(
                JobLogLevel.DEBUG,
                JobLogLevel.INFO,
                JobLogLevel.WARN,
                JobLogLevel.ERROR
        );
    }

    @Test
    @DisplayName("valueOf should return correct enum")
    void valueOfShouldReturnCorrectEnum() {
        assertThat(JobLogLevel.valueOf("DEBUG")).isEqualTo(JobLogLevel.DEBUG);
        assertThat(JobLogLevel.valueOf("INFO")).isEqualTo(JobLogLevel.INFO);
        assertThat(JobLogLevel.valueOf("WARN")).isEqualTo(JobLogLevel.WARN);
        assertThat(JobLogLevel.valueOf("ERROR")).isEqualTo(JobLogLevel.ERROR);
    }

    @Test
    @DisplayName("ordinal should reflect severity order")
    void ordinalShouldReflectSeverityOrder() {
        assertThat(JobLogLevel.DEBUG.ordinal()).isLessThan(JobLogLevel.INFO.ordinal());
        assertThat(JobLogLevel.INFO.ordinal()).isLessThan(JobLogLevel.WARN.ordinal());
        assertThat(JobLogLevel.WARN.ordinal()).isLessThan(JobLogLevel.ERROR.ordinal());
    }
}
