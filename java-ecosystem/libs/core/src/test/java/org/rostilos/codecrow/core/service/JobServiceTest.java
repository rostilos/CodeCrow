package org.rostilos.codecrow.core.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.job.*;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.user.User;
import org.rostilos.codecrow.core.persistence.repository.job.JobLogRepository;
import org.rostilos.codecrow.core.persistence.repository.job.JobRepository;

import java.lang.reflect.Field;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
@DisplayName("JobService")
class JobServiceTest {

    @Mock
    private JobRepository jobRepository;
    @Mock
    private JobLogRepository jobLogRepository;

    private ObjectMapper objectMapper;
    private JobService jobService;

    @BeforeEach
    void setUp() {
        objectMapper = new ObjectMapper();
        jobService = new JobService(jobRepository, jobLogRepository, objectMapper);
    }

    @Nested
    @DisplayName("createPrAnalysisJob()")
    class CreatePrAnalysisJobTests {

        @Test
        @DisplayName("should create PR analysis job")
        void shouldCreatePrAnalysisJob() {
            Project project = createProject(1L, "Test Project");
            User user = createUser(10L, "testuser");

            when(jobRepository.save(any(Job.class))).thenAnswer(inv -> {
                Job j = inv.getArgument(0);
                setField(j, "id", 100L);
                return j;
            });
            when(jobLogRepository.save(any(JobLog.class))).thenAnswer(inv -> inv.getArgument(0));

            Job job = jobService.createPrAnalysisJob(
                    project, 42L, "feature", "main", "abc123",
                    JobTriggerSource.WEBHOOK, user
            );

            assertThat(job.getJobType()).isEqualTo(JobType.PR_ANALYSIS);
            assertThat(job.getPrNumber()).isEqualTo(42L);
            assertThat(job.getBranchName()).isEqualTo("main");
            assertThat(job.getCommitHash()).isEqualTo("abc123");
            assertThat(job.getStatus()).isEqualTo(JobStatus.PENDING);
            assertThat(job.getTitle()).contains("PR #42");

            verify(jobRepository).save(any(Job.class));
            verify(jobLogRepository).save(any(JobLog.class));
        }
    }

    @Nested
    @DisplayName("createBranchAnalysisJob()")
    class CreateBranchAnalysisJobTests {

        @Test
        @DisplayName("should create branch analysis job")
        void shouldCreateBranchAnalysisJob() {
            Project project = createProject(1L, "Test Project");

            when(jobRepository.save(any(Job.class))).thenAnswer(inv -> {
                Job j = inv.getArgument(0);
                setField(j, "id", 101L);
                return j;
            });
            when(jobLogRepository.save(any(JobLog.class))).thenAnswer(inv -> inv.getArgument(0));

            Job job = jobService.createBranchAnalysisJob(
                    project, "develop", "def456",
                    JobTriggerSource.API, null
            );

            assertThat(job.getJobType()).isEqualTo(JobType.BRANCH_ANALYSIS);
            assertThat(job.getBranchName()).isEqualTo("develop");
            assertThat(job.getCommitHash()).isEqualTo("def456");
            assertThat(job.getStatus()).isEqualTo(JobStatus.PENDING);

            verify(jobRepository).save(any(Job.class));
        }
    }

    @Nested
    @DisplayName("createRagIndexJob()")
    class CreateRagIndexJobTests {

        @Test
        @DisplayName("should create initial RAG index job")
        void shouldCreateInitialRagIndexJob() {
            Project project = createProject(1L, "Test");

            when(jobRepository.save(any(Job.class))).thenAnswer(inv -> {
                Job j = inv.getArgument(0);
                setField(j, "id", 102L);
                return j;
            });
            when(jobLogRepository.save(any(JobLog.class))).thenAnswer(inv -> inv.getArgument(0));

            Job job = jobService.createRagIndexJob(project, true, JobTriggerSource.WEBHOOK, null);

            assertThat(job.getJobType()).isEqualTo(JobType.RAG_INITIAL_INDEX);
            assertThat(job.getTitle()).contains("Initial");
        }

        @Test
        @DisplayName("should create incremental RAG index job")
        void shouldCreateIncrementalRagIndexJob() {
            Project project = createProject(1L, "Test");

            when(jobRepository.save(any(Job.class))).thenAnswer(inv -> {
                Job j = inv.getArgument(0);
                setField(j, "id", 103L);
                return j;
            });
            when(jobLogRepository.save(any(JobLog.class))).thenAnswer(inv -> inv.getArgument(0));

            Job job = jobService.createRagIndexJob(project, false, JobTriggerSource.API, null);

            assertThat(job.getJobType()).isEqualTo(JobType.RAG_INCREMENTAL_INDEX);
            assertThat(job.getTitle()).contains("Incremental");
        }
    }

    @Nested
    @DisplayName("createIgnoredCommentJob()")
    class CreateIgnoredCommentJobTests {

        @Test
        @DisplayName("should create ignored comment job with PR number")
        void shouldCreateIgnoredCommentJobWithPrNumber() {
            Project project = createProject(1L, "Test");

            when(jobRepository.save(any(Job.class))).thenAnswer(inv -> {
                Job j = inv.getArgument(0);
                setField(j, "id", 104L);
                return j;
            });
            when(jobLogRepository.save(any(JobLog.class))).thenAnswer(inv -> inv.getArgument(0));

            Job job = jobService.createIgnoredCommentJob(
                    project, 50L, "pr:comment:created", JobTriggerSource.WEBHOOK
            );

            assertThat(job.getJobType()).isEqualTo(JobType.IGNORED_COMMENT);
            assertThat(job.getPrNumber()).isEqualTo(50L);
            assertThat(job.getTitle()).contains("PR #50");
        }

        @Test
        @DisplayName("should create ignored comment job without PR number")
        void shouldCreateIgnoredCommentJobWithoutPrNumber() {
            Project project = createProject(1L, "Test");

            when(jobRepository.save(any(Job.class))).thenAnswer(inv -> {
                Job j = inv.getArgument(0);
                setField(j, "id", 105L);
                return j;
            });
            when(jobLogRepository.save(any(JobLog.class))).thenAnswer(inv -> inv.getArgument(0));

            Job job = jobService.createIgnoredCommentJob(
                    project, null, "repo:comment", JobTriggerSource.WEBHOOK
            );

            assertThat(job.getTitle()).contains("repo:comment");
        }
    }

    @Nested
    @DisplayName("createCommandJob()")
    class CreateCommandJobTests {

        @Test
        @DisplayName("should create summarize command job")
        void shouldCreateSummarizeCommandJob() {
            Project project = createProject(1L, "Test");

            when(jobRepository.save(any(Job.class))).thenAnswer(inv -> {
                Job j = inv.getArgument(0);
                setField(j, "id", 106L);
                return j;
            });
            when(jobLogRepository.save(any(JobLog.class))).thenAnswer(inv -> inv.getArgument(0));

            Job job = jobService.createCommandJob(
                    project, JobType.SUMMARIZE_COMMAND, 60L, "ghi789",
                    JobTriggerSource.API
            );

            assertThat(job.getJobType()).isEqualTo(JobType.SUMMARIZE_COMMAND);
            assertThat(job.getTitle()).contains("Summarize");
        }

        @Test
        @DisplayName("should create ask command job")
        void shouldCreateAskCommandJob() {
            Project project = createProject(1L, "Test");

            when(jobRepository.save(any(Job.class))).thenAnswer(inv -> {
                Job j = inv.getArgument(0);
                setField(j, "id", 107L);
                return j;
            });
            when(jobLogRepository.save(any(JobLog.class))).thenAnswer(inv -> inv.getArgument(0));

            Job job = jobService.createCommandJob(
                    project, JobType.ASK_COMMAND, 61L, "jkl012",
                    JobTriggerSource.API
            );

            assertThat(job.getJobType()).isEqualTo(JobType.ASK_COMMAND);
            assertThat(job.getTitle()).contains("Ask");
        }

        @Test
        @DisplayName("should throw for invalid command job type")
        void shouldThrowForInvalidCommandJobType() {
            Project project = createProject(1L, "Test");

            assertThatThrownBy(() -> jobService.createCommandJob(
                    project, JobType.PR_ANALYSIS, 62L, "mno345",
                    JobTriggerSource.API
            )).isInstanceOf(IllegalArgumentException.class)
              .hasMessageContaining("Invalid command job type");
        }

        @Test
        @DisplayName("should create analyze command job")
        void shouldCreateAnalyzeCommandJob() {
            Project project = createProject(1L, "Test");

            when(jobRepository.save(any(Job.class))).thenAnswer(inv -> {
                Job j = inv.getArgument(0);
                setField(j, "id", 108L);
                return j;
            });
            when(jobLogRepository.save(any(JobLog.class))).thenAnswer(inv -> inv.getArgument(0));

            Job job = jobService.createCommandJob(
                    project, JobType.ANALYZE_COMMAND, 63L, "pqr678",
                    JobTriggerSource.API
            );

            assertThat(job.getTitle()).contains("Analyze");
        }

        @Test
        @DisplayName("should create review command job")
        void shouldCreateReviewCommandJob() {
            Project project = createProject(1L, "Test");

            when(jobRepository.save(any(Job.class))).thenAnswer(inv -> {
                Job j = inv.getArgument(0);
                setField(j, "id", 109L);
                return j;
            });
            when(jobLogRepository.save(any(JobLog.class))).thenAnswer(inv -> inv.getArgument(0));

            Job job = jobService.createCommandJob(
                    project, JobType.REVIEW_COMMAND, 64L, "stu901",
                    JobTriggerSource.API
            );

            assertThat(job.getTitle()).contains("Review");
        }
    }

    @Nested
    @DisplayName("Job Lifecycle")
    class JobLifecycleTests {

        @Test
        @DisplayName("startJob() should transition job to RUNNING")
        void startJobShouldTransitionToRunning() {
            Job job = new Job();
            setField(job, "id", 200L);
            job.setStatus(JobStatus.PENDING);

            when(jobRepository.save(any(Job.class))).thenAnswer(inv -> inv.getArgument(0));
            when(jobLogRepository.save(any(JobLog.class))).thenAnswer(inv -> inv.getArgument(0));

            Job result = jobService.startJob(job);

            assertThat(result.getStatus()).isEqualTo(JobStatus.RUNNING);
            verify(jobLogRepository).save(any(JobLog.class));
        }

        @Test
        @DisplayName("startJob() by external ID should find and start job")
        void startJobByExternalIdShouldFindAndStart() {
            Job job = new Job();
            setField(job, "id", 201L);
            setField(job, "externalId", "ext-123");
            job.setStatus(JobStatus.PENDING);

            when(jobRepository.findByExternalId("ext-123")).thenReturn(Optional.of(job));
            when(jobRepository.save(any(Job.class))).thenAnswer(inv -> inv.getArgument(0));
            when(jobLogRepository.save(any(JobLog.class))).thenAnswer(inv -> inv.getArgument(0));

            Job result = jobService.startJob("ext-123");

            assertThat(result.getStatus()).isEqualTo(JobStatus.RUNNING);
        }

        @Test
        @DisplayName("completeJob() should transition job to COMPLETED")
        void completeJobShouldTransitionToCompleted() {
            Job job = new Job();
            setField(job, "id", 202L);
            job.setStatus(JobStatus.RUNNING);

            when(jobRepository.save(any(Job.class))).thenAnswer(inv -> inv.getArgument(0));
            when(jobLogRepository.save(any(JobLog.class))).thenAnswer(inv -> inv.getArgument(0));

            Job result = jobService.completeJob(job);

            assertThat(result.getStatus()).isEqualTo(JobStatus.COMPLETED);
        }

        @Test
        @DisplayName("completeJob() with CodeAnalysis should link analysis")
        void completeJobWithCodeAnalysisShouldLinkAnalysis() {
            Job job = new Job();
            setField(job, "id", 203L);
            job.setStatus(JobStatus.RUNNING);

            CodeAnalysis analysis = new CodeAnalysis();
            setField(analysis, "id", 500L);

            when(jobRepository.save(any(Job.class))).thenAnswer(inv -> inv.getArgument(0));
            when(jobLogRepository.save(any(JobLog.class))).thenAnswer(inv -> inv.getArgument(0));

            Job result = jobService.completeJob(job, analysis);

            assertThat(result.getStatus()).isEqualTo(JobStatus.COMPLETED);
            assertThat(result.getCodeAnalysis()).isEqualTo(analysis);
        }

        @Test
        @DisplayName("failJob() should transition job to FAILED")
        void failJobShouldTransitionToFailed() {
            Job job = new Job();
            setField(job, "id", 204L);
            job.setStatus(JobStatus.RUNNING);

            when(jobRepository.findById(204L)).thenReturn(Optional.of(job));
            when(jobRepository.save(any(Job.class))).thenAnswer(inv -> inv.getArgument(0));
            when(jobLogRepository.save(any(JobLog.class))).thenAnswer(inv -> inv.getArgument(0));

            Job result = jobService.failJob(job, "Something went wrong");

            assertThat(result.getStatus()).isEqualTo(JobStatus.FAILED);
            assertThat(result.getErrorMessage()).isEqualTo("Something went wrong");
        }

        @Test
        @DisplayName("cancelJob() should transition job to CANCELLED")
        void cancelJobShouldTransitionToCancelled() {
            Job job = new Job();
            setField(job, "id", 205L);
            job.setStatus(JobStatus.RUNNING);

            when(jobRepository.save(any(Job.class))).thenAnswer(inv -> inv.getArgument(0));
            when(jobLogRepository.save(any(JobLog.class))).thenAnswer(inv -> inv.getArgument(0));

            Job result = jobService.cancelJob(job);

            assertThat(result.getStatus()).isEqualTo(JobStatus.CANCELLED);
        }
    }

    // Helper methods

    private Project createProject(Long id, String name) {
        Project project = new Project();
        setField(project, "id", id);
        project.setName(name);
        return project;
    }

    private User createUser(Long id, String username) {
        User user = new User();
        setField(user, "id", id);
        user.setUsername(username);
        return user;
    }

    private void setField(Object obj, String fieldName, Object value) {
        try {
            Field field = obj.getClass().getDeclaredField(fieldName);
            field.setAccessible(true);
            field.set(obj, value);
        } catch (Exception e) {
            throw new RuntimeException("Failed to set field " + fieldName, e);
        }
    }
}
