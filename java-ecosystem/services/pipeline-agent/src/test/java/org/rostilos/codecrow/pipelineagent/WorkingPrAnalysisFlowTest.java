package org.rostilos.codecrow.pipelineagent;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.restassured.RestAssured;
import io.restassured.response.Response;
import jakarta.persistence.EntityManager;
import jakarta.persistence.PersistenceContext;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.mockito.ArgumentCaptor;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequestImpl;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.FileContentDto;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.PrEnrichmentDataDto;
import org.rostilos.codecrow.analysisengine.dto.request.processor.AnalysisProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.analysisengine.service.vcs.ExactHeadAdmission;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsAiClientService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsReportingService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.ai.AIProviderKey;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisMode;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.job.JobStatus;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.ProjectAiConnectionBinding;
import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.EVcsSetupStatus;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;
import org.rostilos.codecrow.core.persistence.repository.job.JobRepository;
import org.rostilos.codecrow.queue.RedisQueueService;
import org.rostilos.codecrow.security.jwt.utils.JwtUtils;
import org.rostilos.codecrow.testsupport.cleanup.DatabaseCleaner;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.boot.test.mock.mockito.SpyBean;
import org.springframework.boot.test.util.TestPropertyValues;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.context.ApplicationContextInitializer;
import org.springframework.context.ConfigurableApplicationContext;
import org.springframework.context.annotation.Import;
import org.springframework.core.io.ClassPathResource;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.annotation.DirtiesContext;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.ContextConfiguration;
import org.springframework.transaction.support.TransactionTemplate;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static io.restassured.RestAssured.given;
import static org.assertj.core.api.Assertions.assertThat;
import static org.awaitility.Awaitility.await;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * One opt-in proof of the shipping PR-analysis spine. PostgreSQL and Redis are
 * real isolated services; VCS/model network boundaries are deterministic fakes.
 */
@EnabledIfEnvironmentVariable(named = "VS01_POSTGRES_HOST", matches = ".+")
@EnabledIfEnvironmentVariable(named = "VS01_REDIS_HOST", matches = ".+")
@SpringBootTest(
        classes = ProcessingApplication.class,
        webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT,
        properties = {
                "spring.jpa.hibernate.ddl-auto=create",
                "spring.flyway.enabled=false",
                "codecrow.pipeline.streaming-response.enabled=true",
                "codecrow.review.delivery.initial-delay-ms=3600000",
                "codecrow.rag.api.enabled=false"
        })
@ActiveProfiles("vs01")
@ContextConfiguration(
        initializers = WorkingPrAnalysisFlowTest.InfrastructureInitializer.class)
@Import(DatabaseCleaner.class)
@DirtiesContext(classMode = DirtiesContext.ClassMode.AFTER_CLASS)
class WorkingPrAnalysisFlowTest {

    private static final long PR_NUMBER = 42L;
    private static final String BASE_SHA =
            "0000000000000000000000000000000000000000";
    private static final String HEAD_SHA =
            "1111111111111111111111111111111111111111";
    private static final String MERGE_BASE_SHA =
            "2222222222222222222222222222222222222222";
    @MockBean
    private VcsServiceFactory vcsServiceFactory;

    @SpyBean
    private RedisQueueService redisQueueService;

    @LocalServerPort
    private int port;

    @Autowired
    private ObjectMapper objectMapper;

    @Autowired
    private JwtUtils jwtUtils;

    @Autowired
    private DatabaseCleaner databaseCleaner;

    @Autowired
    private CodeAnalysisRepository codeAnalysisRepository;

    @Autowired
    private JobRepository jobRepository;

    @Autowired
    private TransactionTemplate transactionTemplate;

    @Autowired
    private JdbcTemplate jdbcTemplate;

    @Autowired
    @Qualifier("queueRedisTemplate")
    private StringRedisTemplate queueRedisTemplate;

    @PersistenceContext
    private EntityManager entityManager;

    private VcsAiClientService vcsAiClientService;
    private VcsReportingService vcsReportingService;

    @BeforeEach
    void setUp() throws Exception {
        databaseCleaner.cleanAll();
        Set<String> queueKeys = queueRedisTemplate.keys("codecrow:*");
        if (queueKeys != null && !queueKeys.isEmpty()) {
            queueRedisTemplate.delete(queueKeys);
        }

        RestAssured.baseURI = "http://127.0.0.1";
        RestAssured.port = port;
        RestAssured.basePath = "";
        RestAssured.enableLoggingOfRequestAndResponseIfValidationFails();

        vcsAiClientService = org.mockito.Mockito.mock(VcsAiClientService.class);
        vcsReportingService = org.mockito.Mockito.mock(VcsReportingService.class);
        when(vcsServiceFactory.getAiClientService(EVcsProvider.GITHUB))
                .thenReturn(vcsAiClientService);
        when(vcsServiceFactory.getReportingService(EVcsProvider.GITHUB))
                .thenReturn(vcsReportingService);
        when(vcsAiClientService.buildExactAiAnalysisRequests(
                any(Project.class),
                any(AnalysisProcessRequest.class),
                any(),
                anyList(),
                any(ExactHeadAdmission.class)))
                .thenAnswer(invocation -> {
                    Project project = invocation.getArgument(0);
                    PrProcessRequest request = invocation.getArgument(1);
                    ExactHeadAdmission admission = invocation.getArgument(4);
                    admission.admit(request.getCommitHash());
                    return List.of(exactRequest(project, request));
                });
    }

    @Test
    void oneExactPullRequestPersistsCompleteCoverageAndDeliversOneFinding()
            throws Exception {
        applyShippingMigrations();
        Long projectId = createProjectWithConnections();
        String token = jwtUtils.generateJwtTokenForUser(
                projectId, String.valueOf(projectId));

        Response response = given()
                .header("Authorization", "Bearer " + token)
                .contentType("application/json")
                .body("""
                    {
                      "projectId": %d,
                      "pullRequestId": %d,
                      "targetBranchName": "main",
                      "sourceBranchName": "feature/working-pr-analysis",
                      "commitHash": "%s",
                      "analysisType": "PR_REVIEW",
                      "prAuthorId": "working-pr-author",
                      "prAuthorUsername": "manifest-author-sentinel"
                    }
                    """.formatted(projectId, PR_NUMBER, HEAD_SHA))
                .when()
                .post("/api/processing/webhook/pr");

        assertThat(response.statusCode()).isEqualTo(200);
        await().atMost(Duration.ofSeconds(60)).untilAsserted(() ->
                assertThat(jobRepository.findLatestJobsForPr(
                        projectId, PR_NUMBER, PageRequest.of(0, 2)))
                        .singleElement()
                        .satisfies(job -> assertThat(job.isTerminal()).isTrue()));

        List<Job> jobs = jobRepository.findLatestJobsForPr(
                projectId, PR_NUMBER, PageRequest.of(0, 2));
        assertThat(jobs).singleElement().satisfies(job -> {
            assertThat(job.getStatus()).as(job.getErrorMessage())
                    .isEqualTo(JobStatus.COMPLETED);
            assertThat(job.getProgress()).isEqualTo(100);
        });

        List<CodeAnalysis> analyses = codeAnalysisRepository
                .findAllByProjectIdAndPrNumberOrderByPrVersionDesc(
                        projectId, PR_NUMBER);
        assertThat(analyses).singleElement().satisfies(analysis -> {
            assertThat(analysis.getCommitHash()).isEqualTo(HEAD_SHA);
            assertThat(analysis.hasExecutionIdentity()).isTrue();
            assertThat(analysis.getIssues()).singleElement().satisfies(issue -> {
                assertThat(issue.getFilePath()).isEqualTo("src/App.java");
                assertThat(issue.getLineNumber()).isEqualTo(5);
                assertThat(issue.getTitle()).isEqualTo("Risky call remains");
            });
        });
        CodeAnalysis analysis = analyses.get(0);

        Map<String, Object> manifest = jdbcTemplate.queryForMap("""
                SELECT id, project_id, pull_request_id, repository_id,
                       base_sha, head_sha, merge_base_sha, policy_version,
                       artifact_manifest_digest, diff_digest, diff_byte_length
                FROM review_execution
                WHERE project_id = ? AND pull_request_id = ?
                """, projectId, PR_NUMBER);
        String executionId = String.valueOf(manifest.get("id"));
        String manifestDigest = String.valueOf(
                manifest.get("artifact_manifest_digest"));
        assertThat(manifest)
                .containsEntry("project_id", projectId)
                .containsEntry("pull_request_id", PR_NUMBER)
                .containsEntry(
                        "repository_id",
                        "github:codecrow-fixtures/working-pr-analysis")
                .containsEntry("base_sha", BASE_SHA)
                .containsEntry("head_sha", HEAD_SHA)
                .containsEntry("merge_base_sha", MERGE_BASE_SHA)
                .containsEntry("policy_version", "candidate-review-v1");
        assertThat(manifestDigest).matches("[0-9a-f]{64}");
        assertThat(String.valueOf(manifest.get("diff_digest")))
                .matches("[0-9a-f]{64}");
        assertThat(analysis.getExecutionId()).isEqualTo(executionId);
        assertThat(analysis.getArtifactManifestDigest()).isEqualTo(manifestDigest);

        byte[] rawDiff = jdbcTemplate.queryForObject("""
                SELECT content_bytes FROM review_artifact
                WHERE execution_id = ? AND kind = 'raw-diff'
                """, byte[].class, executionId);
        assertThat(rawDiff).isEqualTo(
                resource("line-tracking/diffs/pr1.diff")
                        .getBytes(StandardCharsets.UTF_8));
        byte[] enrichmentArtifact = jdbcTemplate.queryForObject("""
                SELECT content_bytes FROM review_artifact
                WHERE execution_id = ? AND kind = 'pr-enrichment'
                """, byte[].class, executionId);
        Map<String, Object> persistedEnrichment = objectMapper.readValue(
                enrichmentArtifact,
                new TypeReference<Map<String, Object>>() { });
        Map<String, Object> persistedReviewContext = objectMapper.convertValue(
                persistedEnrichment.get("reviewContext"),
                new TypeReference<Map<String, Object>>() { });
        assertThat(persistedReviewContext)
                .containsEntry("prTitle", "Working PR context")
                .containsEntry("prAuthor", "manifest-author-sentinel")
                .containsEntry("sourceBranchName", "feature/working-pr-analysis")
                .containsEntry("targetBranchName", "main");

        Map<String, Object> coverage = jdbcTemplate.queryForMap("""
                SELECT analysis_state, inventory_anchor_count,
                       pending_anchor_count, owner_pending_anchor_count,
                       examined_anchor_count, incomplete_anchor_count,
                       unsupported_anchor_count, failed_anchor_count
                FROM review_analysis_state WHERE execution_id = ?
                """, executionId);
        assertThat(coverage)
                .containsEntry("analysis_state", "complete")
                .containsEntry("inventory_anchor_count", 1)
                .containsEntry("pending_anchor_count", 0)
                .containsEntry("owner_pending_anchor_count", 0)
                .containsEntry("examined_anchor_count", 1)
                .containsEntry("incomplete_anchor_count", 0)
                .containsEntry("unsupported_anchor_count", 0)
                .containsEntry("failed_anchor_count", 0);
        assertThat(jdbcTemplate.queryForList("""
                SELECT coverage_state, reason_code
                FROM review_coverage_disposition WHERE execution_id = ?
                """, executionId))
                .singleElement()
                .satisfies(disposition -> assertThat(disposition)
                        .containsEntry("coverage_state", "examined")
                        .containsEntry("reason_code", null));

        Map<String, Object> delivery = jdbcTemplate.queryForMap("""
                SELECT execution_id, head_sha, head_generation, state,
                       attempt_count, idempotency_key, report_artifact_id,
                       report_digest, analysis_truth_digest,
                       provider_receipt_id, delivered_at
                FROM review_delivery_outbox WHERE execution_id = ?
                """, executionId);
        assertThat(delivery)
                .containsEntry("execution_id", executionId)
                .containsEntry("head_sha", HEAD_SHA)
                .containsEntry("head_generation", 1L)
                .containsEntry("state", "DELIVERED")
                .containsEntry("attempt_count", 1);
        assertThat(String.valueOf(delivery.get("idempotency_key")))
                .matches("[0-9a-f]{64}");
        assertThat(String.valueOf(delivery.get("analysis_truth_digest")))
                .matches("[0-9a-f]{64}");
        assertThat(delivery.get("report_artifact_id"))
                .isEqualTo("review-output:" + delivery.get("report_digest"));
        assertThat(delivery.get("provider_receipt_id"))
                .isEqualTo(delivery.get("idempotency_key"));
        assertThat(delivery.get("delivered_at")).isNotNull();

        ArgumentCaptor<CodeAnalysis> delivered =
                ArgumentCaptor.forClass(CodeAnalysis.class);
        verify(vcsReportingService, times(1)).postAnalysisResults(
                delivered.capture(),
                any(Project.class),
                eq(PR_NUMBER),
                anyLong(),
                isNull());
        assertThat(delivered.getValue().getId()).isEqualTo(analysis.getId());
        assertThat(delivered.getValue().getIssues())
                .extracting(issue -> issue.getTitle())
                .containsExactly("Risky call remains");

        ArgumentCaptor<String> queuedPayload =
                ArgumentCaptor.forClass(String.class);
        verify(redisQueueService, times(1)).leftPush(
                eq("codecrow:analysis:jobs"), queuedPayload.capture());
        Map<String, Object> envelope = objectMapper.readValue(
                queuedPayload.getValue(),
                new TypeReference<Map<String, Object>>() { });
        Map<String, Object> queuedRequest = objectMapper.convertValue(
                envelope.get("request"),
                new TypeReference<Map<String, Object>>() { });
        Map<String, Object> queuedManifest = objectMapper.convertValue(
                queuedRequest.get("executionManifest"),
                new TypeReference<Map<String, Object>>() { });
        Map<String, Object> queuedCoverage = objectMapper.convertValue(
                queuedRequest.get("coverageLedger"),
                new TypeReference<Map<String, Object>>() { });
        assertThat(envelope).containsEntry("schemaVersion", 2);
        assertThat(queuedRequest)
                .containsEntry("executionMode", "active")
                .containsEntry("publicationAllowed", true)
                .containsEntry("prTitle", "Working PR context")
                .containsEntry("prAuthor", "manifest-author-sentinel")
                .containsEntry("sourceBranchName", "feature/working-pr-analysis")
                .containsEntry("targetBranchName", "main")
                .containsKeys("executionManifest", "coverageLedger")
                .doesNotContainKeys(
                        "reviewExplorationEnabled",
                        "reviewModelPass",
                        "independentVerification");
        assertThat(queuedManifest)
                .containsEntry("executionId", executionId)
                .containsEntry("artifactManifestDigest", manifestDigest)
                .containsEntry("baseSha", BASE_SHA)
                .containsEntry("headSha", HEAD_SHA)
                .containsEntry("mergeBaseSha", MERGE_BASE_SHA);
        assertThat(queuedCoverage)
                .containsEntry("schemaVersion", 1)
                .containsEntry("executionId", executionId)
                .containsEntry("artifactManifestDigest", manifestDigest)
                .containsEntry("anchorCount", 1);
        assertThat((List<?>) queuedCoverage.get("anchors")).hasSize(1);

        verify(vcsAiClientService, times(1)).buildExactAiAnalysisRequests(
                any(Project.class),
                any(AnalysisProcessRequest.class),
                any(),
                anyList(),
                any(ExactHeadAdmission.class));
        verify(vcsAiClientService, never()).buildAiAnalysisRequests(
                any(Project.class),
                any(AnalysisProcessRequest.class),
                any(),
                anyList());
        assertThat(queueRedisTemplate.opsForList()
                .size("codecrow:analysis:jobs")).isZero();
    }

    private AiAnalysisRequest exactRequest(
            Project project,
            PrProcessRequest request) throws Exception {
        String content = resource("line-tracking/files/pr1/src/App.java");
        String diff = resource("line-tracking/diffs/pr1.diff");
        Map<String, String> taskContext = Map.of(
                "task_key", "CC-42",
                "task_summary", "Keep risky calls out of the request path");
        String projectRules = "[{\"title\":\"Reject risky calls\"}]";
        PrEnrichmentDataDto.ReviewContext reviewContext =
                new PrEnrichmentDataDto.ReviewContext(
                        PrEnrichmentDataDto.CURRENT_REVIEW_CONTEXT_SCHEMA_VERSION,
                        "Working PR context",
                        "Exercise the exact snapshot review path.",
                        request.getPrAuthorUsername(),
                        taskContext,
                        "CC-42 previously introduced the request handler.",
                        projectRules,
                        request.getSourceBranchName(),
                        request.getTargetBranchName());
        PrEnrichmentDataDto enrichment = new PrEnrichmentDataDto(
                List.of(FileContentDto.of("src/App.java", content)),
                List.of(),
                List.of(),
                new PrEnrichmentDataDto.EnrichmentStats(
                        1,
                        1,
                        0,
                        0,
                        content.getBytes(StandardCharsets.UTF_8).length,
                        0,
                        Map.of()),
                reviewContext);

        return AiAnalysisRequestImpl.builder()
                .withProjectId(project.getId())
                .withProjectMetadata(
                        project.getWorkspace().getName(), project.getNamespace())
                .withProjectVcsConnectionBindingInfo(
                        "codecrow-fixtures", "working-pr-analysis")
                .withProjectAiConnection(project.getAiBinding().getAiConnection())
                .withProjectAiConnectionTokenDecrypted("offline-model-key")
                .withPullRequestId(request.getPullRequestId())
                .withAnalysisType(AnalysisType.PR_REVIEW)
                .withAnalysisMode(AnalysisMode.FULL)
                .withPrTitle(reviewContext.prTitle())
                .withPrDescription(reviewContext.prDescription())
                .withTaskContext(taskContext)
                .withTaskHistoryContext(reviewContext.taskHistoryContext())
                .withSourceBranchName(reviewContext.sourceBranchName())
                .withTargetBranchName(reviewContext.targetBranchName())
                .withProjectRules(projectRules)
                .withVcsProvider(EVcsProvider.GITHUB.getId())
                .withChangedFiles(List.of("src/App.java"))
                .withDeletedFiles(List.of())
                .withDiffSnippets(List.of())
                .withRawDiff(diff)
                .withImmutableSnapshot(
                        BASE_SHA, request.getCommitHash(), MERGE_BASE_SHA)
                .withPreviousCommitHash(BASE_SHA)
                .withCurrentCommitHash(request.getCommitHash())
                .withUseLocalMcp(true)
                .withUseMcpTools(false)
                .withMaxAllowedTokens(10_000)
                .withEnrichmentData(enrichment)
                .build();
    }

    private void applyShippingMigrations() throws Exception {
        jdbcTemplate.execute("ALTER TABLE code_analysis "
                + "DROP COLUMN IF EXISTS execution_id CASCADE");
        jdbcTemplate.execute("ALTER TABLE code_analysis "
                + "DROP COLUMN IF EXISTS artifact_manifest_digest CASCADE");
        executeMigration("V2.15.0__immutable_execution_manifest.sql");
        executeMigration("V2.16.0__coverage_ledger.sql");

        Boolean legacyCoordinateConstraint = jdbcTemplate.queryForObject(
                "SELECT EXISTS (SELECT 1 FROM pg_constraint "
                        + "WHERE conname = 'uq_code_analysis_project_commit')",
                Boolean.class);
        if (Boolean.TRUE.equals(legacyCoordinateConstraint)) {
            executeMigration(
                    "V2.17.0__allow_distinct_candidate_executions.sql");
        }
        executeMigration("V2.18.0__review_delivery_outbox.sql");
    }

    private void executeMigration(String migration) throws Exception {
        String sql = new ClassPathResource("db/migration/managed/" + migration)
                .getContentAsString(StandardCharsets.UTF_8);
        jdbcTemplate.execute(sql);
    }

    private String resource(String path) throws Exception {
        var url = Thread.currentThread().getContextClassLoader().getResource(path);
        assertThat(url).as(path).isNotNull();
        return Files.readString(Path.of(url.toURI()));
    }

    private Long createProjectWithConnections() {
        return transactionTemplate.execute(status -> {
            Workspace workspace = new Workspace(
                    "working-pr-ws",
                    "Working PR Workspace",
                    "Functional PR-analysis proof");
            entityManager.persist(workspace);

            Project project = new Project();
            project.setWorkspace(workspace);
            project.setNamespace("working-pr-analysis");
            project.setName("Working PR Analysis");
            entityManager.persist(project);

            VcsConnection vcsConnection = new VcsConnection();
            vcsConnection.setWorkspace(workspace);
            vcsConnection.setConnectionName("Working PR GitHub fixture");
            vcsConnection.setProviderType(EVcsProvider.GITHUB);
            vcsConnection.setConnectionType(EVcsConnectionType.GITHUB_APP);
            vcsConnection.setSetupStatus(EVcsSetupStatus.CONNECTED);
            vcsConnection.setExternalWorkspaceId("working-pr-workspace");
            vcsConnection.setExternalWorkspaceSlug("codecrow-fixtures");
            entityManager.persist(vcsConnection);

            VcsRepoBinding repoBinding = new VcsRepoBinding();
            repoBinding.setWorkspace(workspace);
            repoBinding.setProject(project);
            repoBinding.setVcsConnection(vcsConnection);
            repoBinding.setProvider(EVcsProvider.GITHUB);
            repoBinding.setExternalRepoId("working-pr-analysis-repo");
            repoBinding.setExternalNamespace("codecrow-fixtures");
            repoBinding.setExternalRepoSlug("working-pr-analysis");
            repoBinding.setDisplayName("working-pr-analysis");
            repoBinding.setDefaultBranch("main");
            entityManager.persist(repoBinding);
            project.setVcsRepoBinding(repoBinding);

            AIConnection aiConnection = new AIConnection();
            aiConnection.setWorkspace(workspace);
            aiConnection.setName("Offline scripted model");
            aiConnection.setProviderKey(AIProviderKey.OPENAI);
            aiConnection.setAiModel("working-pr-scripted-model");
            aiConnection.setApiKeyEncrypted("unused-offline-fixture-key");
            entityManager.persist(aiConnection);

            ProjectAiConnectionBinding aiBinding =
                    new ProjectAiConnectionBinding();
            aiBinding.setProject(project);
            aiBinding.setAiConnection(aiConnection);
            entityManager.persist(aiBinding);
            project.setAiConnectionBinding(aiBinding);

            entityManager.flush();
            return project.getId();
        });
    }

    static final class InfrastructureInitializer
            implements ApplicationContextInitializer<ConfigurableApplicationContext> {

        @Override
        public void initialize(ConfigurableApplicationContext context) {
            String postgresHost = required("VS01_POSTGRES_HOST");
            String postgresPort = required("VS01_POSTGRES_PORT");
            String postgresDatabase = required("VS01_POSTGRES_DATABASE");
            String postgresUser = required("VS01_POSTGRES_USER");
            String postgresPassword = required("VS01_POSTGRES_PASSWORD");
            String redisHost = required("VS01_REDIS_HOST");
            String redisPort = required("VS01_REDIS_PORT");

            TestPropertyValues.of(
                    "spring.datasource.url=jdbc:postgresql://" + postgresHost
                            + ':' + postgresPort + '/' + postgresDatabase,
                    "spring.datasource.username=" + postgresUser,
                    "spring.datasource.password=" + postgresPassword,
                    "spring.datasource.driver-class-name=org.postgresql.Driver",
                    "spring.redis.host=" + redisHost,
                    "spring.redis.port=" + redisPort,
                    "codecrow.queue.redis.database=1")
                    .applyTo(context);
        }

        private static String required(String name) {
            String value = System.getenv(name);
            if (value == null || value.isBlank()) {
                throw new IllegalStateException(
                        name + " is required for the working-PR flow");
            }
            return value.trim();
        }
    }
}
