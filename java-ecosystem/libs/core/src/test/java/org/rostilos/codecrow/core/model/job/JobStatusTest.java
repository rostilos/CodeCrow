package org.rostilos.codecrow.core.model.job;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("JobStatus")
class JobStatusTest {

    @Test
    @DisplayName("should have all expected values")
    void shouldHaveAllExpectedValues() {
        JobStatus[] values = JobStatus.values();
        
        assertThat(values).hasSize(8);
        assertThat(values).contains(
                JobStatus.PENDING,
                JobStatus.QUEUED,
                JobStatus.RUNNING,
                JobStatus.COMPLETED,
                JobStatus.FAILED,
                JobStatus.CANCELLED,
                JobStatus.WAITING,
                JobStatus.SKIPPED
        );
    }

    @Test
    @DisplayName("valueOf should return correct enum")
    void valueOfShouldReturnCorrectEnum() {
        assertThat(JobStatus.valueOf("PENDING")).isEqualTo(JobStatus.PENDING);
        assertThat(JobStatus.valueOf("RUNNING")).isEqualTo(JobStatus.RUNNING);
        assertThat(JobStatus.valueOf("COMPLETED")).isEqualTo(JobStatus.COMPLETED);
        assertThat(JobStatus.valueOf("FAILED")).isEqualTo(JobStatus.FAILED);
    }

    @Test
    @DisplayName("name should return string representation")
    void nameShouldReturnStringRepresentation() {
        assertThat(JobStatus.PENDING.name()).isEqualTo("PENDING");
        assertThat(JobStatus.RUNNING.name()).isEqualTo("RUNNING");
        assertThat(JobStatus.SKIPPED.name()).isEqualTo("SKIPPED");
    }
}
