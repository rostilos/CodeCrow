package org.rostilos.codecrow.core.dto.job;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.job.*;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.user.User;
import org.rostilos.codecrow.core.model.workspace.Workspace;

import java.time.OffsetDateTime;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.*;

@DisplayName("JobDTO")
class JobDTOTest {

    private Job job;
    private Project project;
    private Workspace workspace;

    @BeforeEach
    void setUp() {
        workspace = new Workspace();
        workspace.setName("Test Workspace");
        
        project = new Project();
        project.setName("Test Project");
        project.setNamespace("test-ns");
        project.setWorkspace(workspace);
        
        job = new Job();
        job.setProject(project);
        job.setJobType(JobType.PR_ANALYSIS);
        job.setStatus(JobStatus.PENDING);
        job.setTriggerSource(JobTriggerSource.WEBHOOK);
        job.setTitle("Analyze PR #42");
        job.setBranchName("feature/test");
        job.setPrNumber(42L);
        job.setCommitHash("abc123");
        job.setProgress(50);
        job.setCurrentStep("Analyzing");
    }

    @Nested
    @DisplayName("from(Job)")
    class FromJobTests {

        @Test
        @DisplayName("should map basic fields")
        void shouldMapBasicFields() {
            JobDTO dto = JobDTO.from(job);
            
            assertThat(dto.id()).isEqualTo(job.getExternalId());
            assertThat(dto.projectId()).isNull(); // No ID set
            assertThat(dto.projectName()).isEqualTo("Test Project");
            assertThat(dto.projectNamespace()).isEqualTo("test-ns");
            assertThat(dto.workspaceName()).isEqualTo("Test Workspace");
            assertThat(dto.jobType()).isEqualTo(JobType.PR_ANALYSIS);
            assertThat(dto.status()).isEqualTo(JobStatus.PENDING);
            assertThat(dto.triggerSource()).isEqualTo(JobTriggerSource.WEBHOOK);
            assertThat(dto.title()).isEqualTo("Analyze PR #42");
            assertThat(dto.branchName()).isEqualTo("feature/test");
            assertThat(dto.prNumber()).isEqualTo(42L);
            assertThat(dto.commitHash()).isEqualTo("abc123");
            assertThat(dto.progress()).isEqualTo(50);
            assertThat(dto.currentStep()).isEqualTo("Analyzing");
        }

        @Test
        @DisplayName("should handle null triggeredBy")
        void shouldHandleNullTriggeredBy() {
            job.setTriggeredBy(null);
            
            JobDTO dto = JobDTO.from(job);
            
            assertThat(dto.triggeredByUserId()).isNull();
            assertThat(dto.triggeredByUsername()).isNull();
        }

        @Test
        @DisplayName("should map triggeredBy user")
        void shouldMapTriggeredByUser() {
            User user = mock(User.class);
            when(user.getId()).thenReturn(123L);
            when(user.getUsername()).thenReturn("testuser");
            job.setTriggeredBy(user);
            
            JobDTO dto = JobDTO.from(job);
            
            assertThat(dto.triggeredByUserId()).isEqualTo(123L);
            assertThat(dto.triggeredByUsername()).isEqualTo("testuser");
        }

        @Test
        @DisplayName("should handle null codeAnalysis")
        void shouldHandleNullCodeAnalysis() {
            job.setCodeAnalysis(null);
            
            JobDTO dto = JobDTO.from(job);
            
            assertThat(dto.codeAnalysisId()).isNull();
        }

        @Test
        @DisplayName("should map codeAnalysis id")
        void shouldMapCodeAnalysisId() {
            CodeAnalysis analysis = mock(CodeAnalysis.class);
            when(analysis.getId()).thenReturn(456L);
            job.setCodeAnalysis(analysis);
            
            JobDTO dto = JobDTO.from(job);
            
            assertThat(dto.codeAnalysisId()).isEqualTo(456L);
        }

        @Test
        @DisplayName("should calculate durationMs for completed job")
        void shouldCalculateDurationForCompletedJob() {
            OffsetDateTime start = OffsetDateTime.now().minusSeconds(30);
            OffsetDateTime end = OffsetDateTime.now();
            job.setStartedAt(start);
            job.setCompletedAt(end);
            
            JobDTO dto = JobDTO.from(job);
            
            assertThat(dto.durationMs()).isNotNull();
            assertThat(dto.durationMs()).isGreaterThanOrEqualTo(29000L);
            assertThat(dto.durationMs()).isLessThanOrEqualTo(31000L);
        }

        @Test
        @DisplayName("should calculate running durationMs for in-progress job")
        void shouldCalculateRunningDurationForInProgressJob() {
            OffsetDateTime start = OffsetDateTime.now().minusSeconds(10);
            job.setStartedAt(start);
            job.setCompletedAt(null);
            
            JobDTO dto = JobDTO.from(job);
            
            assertThat(dto.durationMs()).isNotNull();
            assertThat(dto.durationMs()).isGreaterThanOrEqualTo(9000L);
        }

        @Test
        @DisplayName("should have null durationMs when not started")
        void shouldHaveNullDurationWhenNotStarted() {
            job.setStartedAt(null);
            job.setCompletedAt(null);
            
            JobDTO dto = JobDTO.from(job);
            
            assertThat(dto.durationMs()).isNull();
        }

        @Test
        @DisplayName("should have null logCount when not provided")
        void shouldHaveNullLogCountWhenNotProvided() {
            JobDTO dto = JobDTO.from(job);
            
            assertThat(dto.logCount()).isNull();
        }
    }

    @Nested
    @DisplayName("from(Job, Long)")
    class FromJobWithLogCountTests {

        @Test
        @DisplayName("should include logCount when provided")
        void shouldIncludeLogCount() {
            JobDTO dto = JobDTO.from(job, 100L);
            
            assertThat(dto.logCount()).isEqualTo(100L);
        }

        @Test
        @DisplayName("should handle null logCount")
        void shouldHandleNullLogCount() {
            JobDTO dto = JobDTO.from(job, null);
            
            assertThat(dto.logCount()).isNull();
        }

        @Test
        @DisplayName("should map all fields with logCount")
        void shouldMapAllFieldsWithLogCount() {
            JobDTO dto = JobDTO.from(job, 50L);
            
            assertThat(dto.projectName()).isEqualTo("Test Project");
            assertThat(dto.logCount()).isEqualTo(50L);
        }
    }

    @Nested
    @DisplayName("Timestamps")
    class TimestampTests {

        @Test
        @DisplayName("should map createdAt")
        void shouldMapCreatedAt() {
            JobDTO dto = JobDTO.from(job);
            
            assertThat(dto.createdAt()).isEqualTo(job.getCreatedAt());
        }

        @Test
        @DisplayName("should map startedAt")
        void shouldMapStartedAt() {
            OffsetDateTime startedAt = OffsetDateTime.now();
            job.setStartedAt(startedAt);
            
            JobDTO dto = JobDTO.from(job);
            
            assertThat(dto.startedAt()).isEqualTo(startedAt);
        }

        @Test
        @DisplayName("should map completedAt")
        void shouldMapCompletedAt() {
            OffsetDateTime completedAt = OffsetDateTime.now();
            job.setCompletedAt(completedAt);
            
            JobDTO dto = JobDTO.from(job);
            
            assertThat(dto.completedAt()).isEqualTo(completedAt);
        }
    }

    @Nested
    @DisplayName("Error Message")
    class ErrorMessageTests {

        @Test
        @DisplayName("should map errorMessage")
        void shouldMapErrorMessage() {
            job.setErrorMessage("Something went wrong");
            
            JobDTO dto = JobDTO.from(job);
            
            assertThat(dto.errorMessage()).isEqualTo("Something went wrong");
        }

        @Test
        @DisplayName("should handle null errorMessage")
        void shouldHandleNullErrorMessage() {
            job.setErrorMessage(null);
            
            JobDTO dto = JobDTO.from(job);
            
            assertThat(dto.errorMessage()).isNull();
        }
    }
}
