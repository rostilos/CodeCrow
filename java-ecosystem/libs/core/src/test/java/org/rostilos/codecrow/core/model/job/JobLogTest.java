package org.rostilos.codecrow.core.model.job;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import java.time.OffsetDateTime;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("JobLog")
class JobLogTest {

    private JobLog jobLog;

    @BeforeEach
    void setUp() {
        jobLog = new JobLog();
    }

    @Nested
    @DisplayName("Default values")
    class DefaultValues {

        @Test
        @DisplayName("should have INFO level by default")
        void shouldHaveInfoLevelByDefault() {
            assertThat(jobLog.getLevel()).isEqualTo(JobLogLevel.INFO);
        }

        @Test
        @DisplayName("should have generated external ID")
        void shouldHaveGeneratedExternalId() {
            assertThat(jobLog.getExternalId()).isNotNull();
            assertThat(jobLog.getExternalId()).hasSize(36);
        }

        @Test
        @DisplayName("should have timestamp set to current time")
        void shouldHaveTimestampSetToCurrentTime() {
            assertThat(jobLog.getTimestamp()).isNotNull();
            assertThat(jobLog.getTimestamp()).isBeforeOrEqualTo(OffsetDateTime.now());
        }

        @Test
        @DisplayName("should have null id by default")
        void shouldHaveNullIdByDefault() {
            assertThat(jobLog.getId()).isNull();
        }
    }

    @Nested
    @DisplayName("Basic properties")
    class BasicProperties {

        @Test
        @DisplayName("should set and get job")
        void shouldSetAndGetJob() {
            Job job = new Job();
            job.setTitle("Test Job");
            
            jobLog.setJob(job);
            
            assertThat(jobLog.getJob()).isEqualTo(job);
        }

        @Test
        @DisplayName("should set and get sequenceNumber")
        void shouldSetAndGetSequenceNumber() {
            jobLog.setSequenceNumber(42L);
            assertThat(jobLog.getSequenceNumber()).isEqualTo(42L);
        }

        @Test
        @DisplayName("should set and get level")
        void shouldSetAndGetLevel() {
            jobLog.setLevel(JobLogLevel.ERROR);
            assertThat(jobLog.getLevel()).isEqualTo(JobLogLevel.ERROR);
        }

        @Test
        @DisplayName("should set and get step")
        void shouldSetAndGetStep() {
            jobLog.setStep("analysis");
            assertThat(jobLog.getStep()).isEqualTo("analysis");
        }

        @Test
        @DisplayName("should set and get message")
        void shouldSetAndGetMessage() {
            jobLog.setMessage("Processing completed successfully");
            assertThat(jobLog.getMessage()).isEqualTo("Processing completed successfully");
        }

        @Test
        @DisplayName("should set and get metadata")
        void shouldSetAndGetMetadata() {
            String json = "{\"key\":\"value\"}";
            jobLog.setMetadata(json);
            assertThat(jobLog.getMetadata()).isEqualTo(json);
        }

        @Test
        @DisplayName("should set and get durationMs")
        void shouldSetAndGetDurationMs() {
            jobLog.setDurationMs(1500L);
            assertThat(jobLog.getDurationMs()).isEqualTo(1500L);
        }

        @Test
        @DisplayName("should set and get externalId")
        void shouldSetAndGetExternalId() {
            jobLog.setExternalId("custom-external-id");
            assertThat(jobLog.getExternalId()).isEqualTo("custom-external-id");
        }
    }

    @Nested
    @DisplayName("All log levels")
    class AllLogLevels {

        @Test
        @DisplayName("should support all log levels")
        void shouldSupportAllLogLevels() {
            for (JobLogLevel level : JobLogLevel.values()) {
                jobLog.setLevel(level);
                assertThat(jobLog.getLevel()).isEqualTo(level);
            }
        }
    }
}
