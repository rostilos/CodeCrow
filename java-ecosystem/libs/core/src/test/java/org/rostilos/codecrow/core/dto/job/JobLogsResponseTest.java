package org.rostilos.codecrow.core.dto.job;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.Collections;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("JobLogsResponse")
class JobLogsResponseTest {

    @Test
    @DisplayName("should create record with all fields")
    void shouldCreateRecordWithAllFields() {
        List<JobLogDTO> logs = Collections.emptyList();
        
        JobLogsResponse response = new JobLogsResponse(
                "job-123",
                logs,
                100L,
                true
        );
        
        assertThat(response.jobId()).isEqualTo("job-123");
        assertThat(response.logs()).isEqualTo(logs);
        assertThat(response.latestSequence()).isEqualTo(100L);
        assertThat(response.isComplete()).isTrue();
    }

    @Test
    @DisplayName("should handle incomplete job")
    void shouldHandleIncompleteJob() {
        JobLogsResponse response = new JobLogsResponse(
                "job-456",
                Collections.emptyList(),
                50L,
                false
        );
        
        assertThat(response.jobId()).isEqualTo("job-456");
        assertThat(response.isComplete()).isFalse();
    }

    @Test
    @DisplayName("should handle null values")
    void shouldHandleNullValues() {
        JobLogsResponse response = new JobLogsResponse(null, null, null, false);
        
        assertThat(response.jobId()).isNull();
        assertThat(response.logs()).isNull();
        assertThat(response.latestSequence()).isNull();
    }

    @Test
    @DisplayName("should implement equals correctly")
    void shouldImplementEqualsCorrectly() {
        List<JobLogDTO> logs = Collections.emptyList();
        
        JobLogsResponse response1 = new JobLogsResponse("job-1", logs, 10L, true);
        JobLogsResponse response2 = new JobLogsResponse("job-1", logs, 10L, true);
        JobLogsResponse response3 = new JobLogsResponse("job-2", logs, 10L, true);
        
        assertThat(response1).isEqualTo(response2);
        assertThat(response1).isNotEqualTo(response3);
    }
}
