package org.rostilos.codecrow.core.model.job;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("JobTriggerSource")
class JobTriggerSourceTest {

    @Test
    @DisplayName("should have all expected values")
    void shouldHaveAllExpectedValues() {
        JobTriggerSource[] values = JobTriggerSource.values();
        
        assertThat(values).hasSize(6);
        assertThat(values).contains(
                JobTriggerSource.WEBHOOK,
                JobTriggerSource.PIPELINE,
                JobTriggerSource.API,
                JobTriggerSource.UI,
                JobTriggerSource.SCHEDULED,
                JobTriggerSource.CHAINED
        );
    }

    @Test
    @DisplayName("valueOf should return correct enum")
    void valueOfShouldReturnCorrectEnum() {
        assertThat(JobTriggerSource.valueOf("WEBHOOK")).isEqualTo(JobTriggerSource.WEBHOOK);
        assertThat(JobTriggerSource.valueOf("PIPELINE")).isEqualTo(JobTriggerSource.PIPELINE);
        assertThat(JobTriggerSource.valueOf("API")).isEqualTo(JobTriggerSource.API);
        assertThat(JobTriggerSource.valueOf("UI")).isEqualTo(JobTriggerSource.UI);
    }

    @Test
    @DisplayName("name should return string representation")
    void nameShouldReturnStringRepresentation() {
        assertThat(JobTriggerSource.WEBHOOK.name()).isEqualTo("WEBHOOK");
        assertThat(JobTriggerSource.SCHEDULED.name()).isEqualTo("SCHEDULED");
    }
}
