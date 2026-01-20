package org.rostilos.codecrow.core.dto.job;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.job.JobLogLevel;

import java.time.OffsetDateTime;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("JobLogDTO")
class JobLogDTOTest {

    @Test
    @DisplayName("should create record with all fields")
    void shouldCreateRecordWithAllFields() {
        OffsetDateTime timestamp = OffsetDateTime.now();
        
        JobLogDTO dto = new JobLogDTO(
                "log-123",
                1L,
                JobLogLevel.INFO,
                "Analysis",
                "Starting analysis",
                "{\"files\": 10}",
                1500L,
                timestamp
        );
        
        assertThat(dto.id()).isEqualTo("log-123");
        assertThat(dto.sequenceNumber()).isEqualTo(1L);
        assertThat(dto.level()).isEqualTo(JobLogLevel.INFO);
        assertThat(dto.step()).isEqualTo("Analysis");
        assertThat(dto.message()).isEqualTo("Starting analysis");
        assertThat(dto.metadata()).isEqualTo("{\"files\": 10}");
        assertThat(dto.durationMs()).isEqualTo(1500L);
        assertThat(dto.timestamp()).isEqualTo(timestamp);
    }

    @Test
    @DisplayName("should handle null values")
    void shouldHandleNullValues() {
        JobLogDTO dto = new JobLogDTO(null, null, null, null, null, null, null, null);
        
        assertThat(dto.id()).isNull();
        assertThat(dto.sequenceNumber()).isNull();
        assertThat(dto.level()).isNull();
        assertThat(dto.step()).isNull();
        assertThat(dto.message()).isNull();
        assertThat(dto.metadata()).isNull();
        assertThat(dto.durationMs()).isNull();
        assertThat(dto.timestamp()).isNull();
    }

    @Test
    @DisplayName("should implement equals correctly")
    void shouldImplementEqualsCorrectly() {
        OffsetDateTime timestamp = OffsetDateTime.now();
        
        JobLogDTO dto1 = new JobLogDTO("log-1", 1L, JobLogLevel.INFO, "Step", "Message", null, 100L, timestamp);
        JobLogDTO dto2 = new JobLogDTO("log-1", 1L, JobLogLevel.INFO, "Step", "Message", null, 100L, timestamp);
        JobLogDTO dto3 = new JobLogDTO("log-2", 1L, JobLogLevel.INFO, "Step", "Message", null, 100L, timestamp);
        
        assertThat(dto1).isEqualTo(dto2);
        assertThat(dto1).isNotEqualTo(dto3);
    }
}
