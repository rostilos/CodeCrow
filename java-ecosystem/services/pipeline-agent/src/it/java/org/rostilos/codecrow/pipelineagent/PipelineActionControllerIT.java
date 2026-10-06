package org.rostilos.codecrow.pipelineagent;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.job.JobStatus;
import org.rostilos.codecrow.core.model.job.JobTriggerSource;
import org.rostilos.codecrow.core.model.job.JobType;
import org.rostilos.codecrow.core.persistence.repository.job.JobRepository;
import org.rostilos.codecrow.core.service.JobService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.test.context.TestPropertySource;

import java.time.Duration;

import static io.restassured.RestAssured.given;
import static org.hamcrest.Matchers.*;
import static org.assertj.core.api.Assertions.assertThat;
import static org.awaitility.Awaitility.await;

/**
 * Integration tests for ProviderPipelineActionController.
 * Processing endpoints require project-level JWT authentication.
 * The JWT subject must be the project ID, and the request body's
 * projectId must match the JWT's subject.
 */
@TestPropertySource(properties = "codecrow.pipeline.streaming-response.enabled=false")
class PipelineActionControllerIT extends BasePipelineAgentIT {

    @Autowired
    private JobRepository jobRepository;

    @Autowired
    private JobService jobService;

    private void assertDurableAcceptedJob(String jobId, Long projectId, JobType type,
                                         String commitHash) throws Exception {
        var job = jobRepository.findByExternalId(jobId).orElseThrow();
        assertThat(job.getProject().getId()).isEqualTo(projectId);
        assertThat(job.getJobType()).isEqualTo(type);
        assertThat(job.getTriggerSource()).isEqualTo(JobTriggerSource.PIPELINE);
        assertThat(job.getCommitHash()).isEqualTo(commitHash);
        var payload = objectMapper.readTree(jobService.findWebhookDispatchPayload(job.getId()).orElseThrow());
        assertThat(payload.path("projectId").asLong()).isEqualTo(projectId);
        assertThat(payload.path("commitHash").asText()).isEqualTo(commitHash);
        assertThat(payload.path("targetBranchName").asText()).isEqualTo("main");
        // These projects have no VCS connection. The accepted worker must still
        // finish observably, rather than leave an init-only job or leak work
        // into the next test's database cleanup.
        await().atMost(Duration.ofSeconds(10)).untilAsserted(() ->
                assertThat(jobRepository.findById(job.getId()).orElseThrow().getStatus())
                        .isIn(JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.SKIPPED));
    }

    @Nested
    @DisplayName("POST /api/processing/webhook/pr")
    class PrProcessing {

        @Test
        @DisplayName("PR processing — unauthenticated — 401")
        void prProcessing_noAuth_returns401() {
            unauthenticatedRequest()
                .body("""
                    {
                        "projectId": 1,
                        "pullRequestId": 42,
                        "sourceBranchName": "feature/test",
                        "targetBranchName": "main",
                        "commitHash": "abc123",
                        "analysisType": "PR_REVIEW",
                        "rawDiff": "diff content",
                        "changedFiles": []
                    }
                    """)
            .when()
                .post("/api/processing/webhook/pr")
            .then()
                .statusCode(401);
        }

        @Test
        @DisplayName("PR processing — invalid JWT — 401")
        void prProcessing_invalidToken_returns401() {
            given()
                .header("Authorization", "Bearer invalid.jwt.token")
                .contentType("application/json")
                .body("""
                    {
                        "projectId": 1,
                        "pullRequestId": 42,
                        "sourceBranchName": "feature/test",
                        "targetBranchName": "main"
                    }
                    """)
            .when()
                .post("/api/processing/webhook/pr")
            .then()
                .statusCode(401);
        }

        @Test
        @DisplayName("PR processing — JWT for non-existent project — 404")
        void prProcessing_nonExistentProject_returns404() {
            projectAuthRequest(99999L)
                .body("""
                    {
                        "projectId": 99999,
                        "pullRequestId": 42,
                        "sourceBranchName": "feature/test",
                        "targetBranchName": "main",
                        "commitHash": "abc123def456",
                        "analysisType": "PR_REVIEW"
                    }
                    """)
            .when()
                .post("/api/processing/webhook/pr")
            .then()
                .statusCode(404);
        }

        @Test
        @DisplayName("PR processing — project ID mismatch — 401")
        void prProcessing_projectIdMismatch_returns401() {
            Long projectId = createTestProject("pr-mismatch-ns", "Mismatch Project");

            // JWT subject is projectId but body says different projectId
            projectAuthRequest(projectId)
                .body("""
                    {
                        "projectId": 99999,
                        "pullRequestId": 42,
                        "sourceBranchName": "feature/test",
                        "targetBranchName": "main",
                        "commitHash": "abc123def456",
                        "analysisType": "PR_REVIEW"
                    }
                    """)
            .when()
                .post("/api/processing/webhook/pr")
            .then()
                .statusCode(401);

            assertThat(jobRepository.count()).isZero();
        }

        @Test
        @DisplayName("PR processing — valid auth — 202 with durable dispatch")
        void prProcessing_validAuth_accepted() throws Exception {
            Long projectId = createTestProject("pr-valid-ns", "Valid PR Project");

            String jobId = projectAuthRequest(projectId)
                .body("""
                    {
                        "projectId": %d,
                        "pullRequestId": 42,
                        "sourceBranchName": "feature/test",
                        "targetBranchName": "main",
                        "commitHash": "abc123def456",
                        "analysisType": "PR_REVIEW",
                        "rawDiff": "diff --git a/file.txt b/file.txt",
                        "changedFiles": [{"path": "file.txt", "status": "modified"}]
                    }
                    """.formatted(projectId))
            .when()
                .post("/api/processing/webhook/pr")
            .then()
                .statusCode(202)
                .body("status", equalTo("accepted"))
                .body("jobId", not(emptyOrNullString()))
                .extract().path("jobId");

            assertDurableAcceptedJob(jobId, projectId, JobType.PR_ANALYSIS, "abc123def456");
        }
    }

    @Nested
    @DisplayName("POST /api/processing/webhook/branch")
    class BranchProcessing {

        @Test
        @DisplayName("Branch processing — unauthenticated — 401")
        void branchProcessing_noAuth_returns401() {
            unauthenticatedRequest()
                .body("""
                    {
                        "projectId": 1,
                        "targetBranchName": "main",
                        "commitHash": "abc123",
                        "analysisType": "BRANCH_ANALYSIS"
                    }
                    """)
            .when()
                .post("/api/processing/webhook/branch")
            .then()
                .statusCode(401);
        }

        @Test
        @DisplayName("Branch processing — invalid JWT — 401")
        void branchProcessing_invalidToken_returns401() {
            given()
                .header("Authorization", "Bearer not-a-valid-jwt")
                .contentType("application/json")
                .body("""
                    {
                        "projectId": 1,
                        "targetBranchName": "main"
                    }
                    """)
            .when()
                .post("/api/processing/webhook/branch")
            .then()
                .statusCode(401);
        }

        @Test
        @DisplayName("Branch processing — JWT for non-existent project — 404")
        void branchProcessing_nonExistentProject_returns404() {
            projectAuthRequest(88888L)
                .body("""
                    {
                        "projectId": 88888,
                        "targetBranchName": "main"
                    }
                    """)
            .when()
                .post("/api/processing/webhook/branch")
            .then()
                .statusCode(404);
        }

        @Test
        @DisplayName("Branch processing — valid auth — 202 with durable dispatch")
        void branchProcessing_validAuth_accepted() throws Exception {
            Long projectId = createTestProject("branch-valid-ns", "Valid Branch Project");

            String jobId = projectAuthRequest(projectId)
                .body("""
                    {
                        "projectId": %d,
                        "targetBranchName": "main",
                        "commitHash": "def456abc789",
                        "analysisType": "BRANCH_ANALYSIS"
                    }
                    """.formatted(projectId))
            .when()
                .post("/api/processing/webhook/branch")
            .then()
                .statusCode(202)
                .body("status", equalTo("accepted"))
                .body("jobId", not(emptyOrNullString()))
                .extract().path("jobId");

            assertDurableAcceptedJob(jobId, projectId, JobType.BRANCH_ANALYSIS, "def456abc789");
        }
    }
}
