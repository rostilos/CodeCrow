package org.rostilos.codecrow.core.dto.job;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.Collections;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("JobListResponse")
class JobListResponseTest {

    @Test
    @DisplayName("should create record with all fields")
    void shouldCreateRecordWithAllFields() {
        List<JobDTO> jobs = Collections.emptyList();
        
        JobListResponse response = new JobListResponse(
                jobs,
                1,
                20,
                100L,
                5
        );
        
        assertThat(response.jobs()).isEqualTo(jobs);
        assertThat(response.page()).isEqualTo(1);
        assertThat(response.pageSize()).isEqualTo(20);
        assertThat(response.totalElements()).isEqualTo(100L);
        assertThat(response.totalPages()).isEqualTo(5);
    }

    @Test
    @DisplayName("should handle empty jobs list")
    void shouldHandleEmptyJobsList() {
        JobListResponse response = new JobListResponse(
                Collections.emptyList(),
                0,
                10,
                0L,
                0
        );
        
        assertThat(response.jobs()).isEmpty();
        assertThat(response.totalElements()).isZero();
    }

    @Test
    @DisplayName("should handle null jobs list")
    void shouldHandleNullJobsList() {
        JobListResponse response = new JobListResponse(null, 0, 10, 0L, 0);
        
        assertThat(response.jobs()).isNull();
    }

    @Test
    @DisplayName("should implement equals correctly")
    void shouldImplementEqualsCorrectly() {
        List<JobDTO> jobs = Collections.emptyList();
        
        JobListResponse response1 = new JobListResponse(jobs, 1, 20, 100L, 5);
        JobListResponse response2 = new JobListResponse(jobs, 1, 20, 100L, 5);
        JobListResponse response3 = new JobListResponse(jobs, 2, 20, 100L, 5);
        
        assertThat(response1).isEqualTo(response2);
        assertThat(response1).isNotEqualTo(response3);
    }
}
