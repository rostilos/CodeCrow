package org.rostilos.codecrow.pipelineagent.reviewfeedback;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.*;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.EnumSource;
import org.mockito.ArgumentCaptor;
import org.rostilos.codecrow.analysisengine.aiclient.AiAnalysisClient;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.analysisengine.service.pr.PullRequestDiffPreparationService;
import org.rostilos.codecrow.analysisengine.util.AnalysisLimitEnforcer;
import org.rostilos.codecrow.core.model.ai.*;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.project.*;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.vcs.*;
import org.rostilos.codecrow.core.model.workspace.*;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.core.persistence.repository.workspace.*;
import org.rostilos.codecrow.pipelineagent.generic.service.AbstractVcsAiClientService;
import org.rostilos.codecrow.queue.RedisQueueService;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.security.service.UserDetailsImpl;
import org.rostilos.codecrow.security.web.WorkspaceSecurity;
import org.rostilos.codecrow.testsupport.containers.SharedPostgresContainer;
import org.rostilos.codecrow.vcsclient.*;
import org.rostilos.codecrow.vcsclient.model.VcsPullRequest;
import org.rostilos.codecrow.webserver.reviewfeedback.ReviewFeedbackService;
import org.rostilos.codecrow.webserver.reviewfeedback.ReviewFeedbackController;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.Authentication;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.server.ResponseStatusException;

import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.*;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import static org.rostilos.codecrow.webserver.reviewfeedback.ReviewFeedbackService.*;

/** Production migration + JDBC transactions + actual generic request/queue serialization.
 * Only remote VCS/Redis transport and membership repository adapters are test doubles.
 */
class ReviewFeedbackLearningIT {
    private final ObjectMapper mapper = new ObjectMapper().findAndRegisterModules();
    private JdbcTemplate jdbc;
    private DriverManagerDataSource dataSource;
    private WorkspaceSecurity security;
    private ReviewFeedbackService service;
    private String schema;
    private static final String HEAD = "2222222222222222222222222222222222222222";
    private static final String BASE = "1111111111111111111111111111111111111111";
    private static final String DIFF = "diff --git a/src/App.java b/src/App.java\n--- a/src/App.java\n+++ b/src/App.java\n@@ -1 +1 @@\n-old\n+new\n";

    @BeforeEach void setup() throws Exception {
        var pg = SharedPostgresContainer.getInstance();
        schema = "feedback_" + UUID.randomUUID().toString().replace("-", "");
        var bootstrap = new DriverManagerDataSource(pg.getJdbcUrl(), pg.getUsername(), pg.getPassword());
        new JdbcTemplate(bootstrap).execute("CREATE SCHEMA " + schema);
        dataSource = new DriverManagerDataSource(pg.getJdbcUrl() + (pg.getJdbcUrl().contains("?") ? "&" : "?")
                + "currentSchema=" + schema, pg.getUsername(), pg.getPassword());
        jdbc = new JdbcTemplate(dataSource);
        jdbc.execute("CREATE TABLE workspace(id bigint PRIMARY KEY,slug text)");
        jdbc.execute("CREATE TABLE project(id bigint PRIMARY KEY,workspace_id bigint,namespace text,configuration jsonb,updated_at timestamptz)");
        jdbc.execute("CREATE TABLE code_analysis(id bigint PRIMARY KEY,project_id bigint,commit_hash varchar(64))");
        jdbc.execute("CREATE TABLE code_analysis_issue(id bigint PRIMARY KEY,analysis_id bigint,title text,file_path text,issue_category text)");
        jdbc.execute("CREATE TABLE workspace_member(workspace_id bigint,user_id bigint,role text,status text)");
        try (var sql = getClass().getResourceAsStream("/db/migration/managed/V2.34.0__review_feedback_learning.sql")) {
            assertThat(sql).isNotNull(); jdbc.execute(new String(sql.readAllBytes(), StandardCharsets.UTF_8));
        }
        jdbc.update("INSERT INTO workspace VALUES (1,'one'),(2,'two')");
        jdbc.update("INSERT INTO project VALUES (10,1,'repo',CAST(? AS jsonb),now()),(20,2,'repo','{}',now())", """
                {"useMcpTools":true,"unknownSetting":"preserved","projectRules":{"rules":[
                  {"id":"manual","title":"Manual rule","description":"Keep manual rule","ruleType":"ENFORCE","enabled":true,"priority":0}
                ]}}
                """);
        jdbc.update("INSERT INTO code_analysis VALUES (100,10,?),(101,10,?),(200,20,?)", BASE, HEAD, HEAD);
        jdbc.update("INSERT INTO code_analysis_issue VALUES (1000,100,'Validate request before use','src/App.java','SECURITY'),"
                + "(1001,101,'  VALIDATE request before use  ','src/App.java','SECURITY'),"
                + "(2000,200,'Validate request before use','src/App.java','SECURITY')");
        jdbc.update("INSERT INTO workspace_member VALUES (1,1,'OWNER','ACTIVE'),(1,2,'MEMBER','ACTIVE'),"
                + "(1,3,'REVIEWER','ACTIVE'),(1,4,'OWNER','PENDING'),(2,1,'OWNER','ACTIVE'),(2,5,'OWNER','ACTIVE')");
        // Exercise the real WorkspaceSecurity decision logic, backed by the isolated fixture rows.
        var workspaces = mock(WorkspaceRepository.class);
        when(workspaces.findBySlug(anyString())).thenAnswer(call -> jdbc.query("SELECT id,slug FROM workspace WHERE slug=?",
                (rs, row) -> { Workspace w = mock(Workspace.class); when(w.getId()).thenReturn(rs.getLong(1));
                    when(w.getName()).thenReturn(rs.getString(2)); return w; }, call.getArgument(0, String.class)).stream().findFirst());
        var projects = mock(ProjectRepository.class);
        when(projects.findByWorkspaceIdAndNamespace(anyLong(), anyString())).thenAnswer(call -> jdbc.query(
                "SELECT id FROM project WHERE workspace_id=? AND namespace=?", (rs, row) -> {
                    Project p = mock(Project.class); when(p.getId()).thenReturn(rs.getLong(1)); return p;
                }, call.getArgument(0), call.getArgument(1)).stream().findFirst());
        var members = mock(WorkspaceMemberRepository.class);
        when(members.findByWorkspaceIdAndUserId(anyLong(), anyLong())).thenAnswer(call -> jdbc.query(
                "SELECT role,status FROM workspace_member WHERE workspace_id=? AND user_id=?", (rs, row) -> {
                    var m = new WorkspaceMember(); m.setRole(EWorkspaceRole.valueOf(rs.getString(1)));
                    m.setStatus(EMembershipStatus.valueOf(rs.getString(2))); return m;
                }, call.getArgument(0), call.getArgument(1)).stream().findFirst());
        security = new WorkspaceSecurity(members, workspaces, projects);
        service = service(20);
    }

    @AfterEach void cleanup() { if (jdbc != null) jdbc.execute("DROP SCHEMA " + schema + " CASCADE"); }
    private ReviewFeedbackService service(int limit) {
        return new ReviewFeedbackService(jdbc, security, mapper, new DataSourceTransactionManager(dataSource), limit);
    }
    private Authentication user(long id) {
        return UsernamePasswordAuthenticationToken.authenticated(new UserDetailsImpl(id, "user" + id,
                "user@example.invalid", "", null, List.of()), "", List.of());
    }
    private RuleView feedback(long issue, Verdict verdict, long user) {
        return service.record("one", "repo", issue, verdict, "Validated against the repository contract", user(user));
    }

    @ParameterizedTest @EnumSource(EVcsProvider.class)
    void feedbackFlowsThroughTheActualPrAndBranchRequestIntoQueuePayload(EVcsProvider provider) throws Exception {
        var candidate = feedback(1000, Verdict.FALSE_POSITIVE, 2);
        assertThat(candidate.state()).isEqualTo(State.CANDIDATE);
        assertThat(candidate.effective()).isFalse();
        var active = service.promote("one", "repo", candidate.id(), null, user(1));
        assertThat(active.effective()).isTrue();
        assertThat(active.promotedBy()).isEqualTo(1);
        assertThat(active.evidence()).singleElement().satisfies(e -> {
            assertThat(e.issueId()).isEqualTo(1000); assertThat(e.analysisId()).isEqualTo(100);
            assertThat(e.userId()).isEqualTo(2); assertThat(e.commitHash()).isEqualTo(BASE);
        });
        service.promote("one", "repo", candidate.id(), null, user(1));
        assertThat(service(20).list("one", "repo", user(1))).singleElement()
                .satisfies(rule -> assertThat(rule.effective()).isTrue());
        for (AiAnalysisRequest request : requests(provider)) {
            JsonNode rules = mapper.readTree(request.getProjectRules());
            assertThat(rules.size()).isEqualTo(2);
            assertThat(rules.get(1).path("id").asText()).isEqualTo("learned:" + candidate.id());
            assertThat(rules.get(1).path("ruleType").asText()).isEqualTo("SUPPRESS");
            assertThat(rules.get(1).path("filePatterns").get(0).asText()).isEqualTo("src/App.java");
            var queue = mock(RedisQueueService.class);
            when(queue.hasKey("codecrow:analysis:consumer:heartbeat")).thenReturn(true);
            when(queue.rightPop(anyString(), anyLong())).thenReturn("{\"type\":\"final\",\"result\":{\"comment\":\"ok\",\"issues\":[]}}");
            new AiAnalysisClient(mock(RestTemplate.class), queue, mapper).performAnalysis(request);
            var payload = ArgumentCaptor.forClass(String.class);
            verify(queue).leftPush(eq("codecrow:analysis:jobs"), payload.capture());
            assertThat(mapper.readTree(payload.getValue()).path("request").path("projectRules").asText())
                    .isEqualTo(request.getProjectRules());
        }
    }

    @Test void contradictionsRetractOnlyLearnedRuleAndNeedAnotherOwnerPromotion() throws Exception {
        var first = feedback(1000, Verdict.FALSE_POSITIVE, 2);
        service.promote("one", "repo", first.id(), null, user(1));
        var contested = feedback(1000, Verdict.VALID, 3);
        assertThat(contested.state()).isEqualTo(State.CONTESTED);
        assertThat(contested.effective()).isFalse();
        assertThat(contested.validVotes()).isEqualTo(1); assertThat(contested.falsePositiveVotes()).isEqualTo(1);
        assertThatThrownBy(() -> service.promote("one", "repo", first.id(), null, user(1)))
                .isInstanceOf(ResponseStatusException.class).hasMessageContaining("409");
        assertOnlyManualRule();
        var resolved = feedback(1000, Verdict.FALSE_POSITIVE, 3);
        assertThat(resolved.state()).isEqualTo(State.CANDIDATE);
        assertThat(resolved.evidence()).hasSize(3); assertThat(resolved.validVotes()).isZero();
        assertThat(resolved.falsePositiveVotes()).isEqualTo(2);
        service.promote("one", "repo", first.id(), null, user(1));
        service.disable("one", "repo", first.id(), user(1));
        assertThat(feedback(1000, Verdict.FALSE_POSITIVE, 3).state()).isEqualTo(State.DISABLED);
        assertOnlyManualRule();
    }

    @Test void changedSoleOpinionRetractsAndRegeneratesOppositeCandidateWithoutLosingHistory() {
        var first = feedback(1000, Verdict.FALSE_POSITIVE, 2);
        service.promote("one", "repo", first.id(), null, user(1));
        var changed = feedback(1000, Verdict.VALID, 2);
        assertThat(changed.effective()).isFalse(); assertThat(changed.state()).isEqualTo(State.CANDIDATE);
        assertThat(changed.ruleType().name()).isEqualTo("ENFORCE");
        assertThat(changed.description()).startsWith("Check for independently verified");
        assertThat(changed.evidence()).hasSize(2); assertThat(changed.validVotes()).isEqualTo(1);
        assertThat(changed.falsePositiveVotes()).isZero();
    }

    @Test void scopeAndMembershipApplyAtEveryServiceBoundaryBeforeAnyMutation() {
        var rule = feedback(1000, Verdict.VALID, 2);
        assertThatThrownBy(() -> feedback(2000, Verdict.FALSE_POSITIVE, 1)).isInstanceOf(ResponseStatusException.class)
                .hasMessageContaining("404");
        assertThatThrownBy(() -> service.promote("two", "repo", rule.id(), null, user(1)))
                .isInstanceOf(ResponseStatusException.class).hasMessageContaining("404");
        assertThatThrownBy(() -> service.disable("two", "repo", rule.id(), user(1)))
                .isInstanceOf(ResponseStatusException.class).hasMessageContaining("404");
        for (long id : List.of(2L, 3L, 4L, 5L, 999L)) {
            assertThatThrownBy(() -> service.promote("one", "repo", rule.id(), null, user(id))).isInstanceOf(AccessDeniedException.class);
            assertThatThrownBy(() -> service.disable("one", "repo", rule.id(), user(id))).isInstanceOf(AccessDeniedException.class);
            assertThatThrownBy(() -> service.list("one", "repo", user(id))).isInstanceOf(AccessDeniedException.class);
        }
        for (long id : List.of(4L, 5L, 999L)) {
            assertThatThrownBy(() -> feedback(1000, Verdict.VALID, id)).isInstanceOf(AccessDeniedException.class);
        }
        assertThatThrownBy(() -> service.record("one", "repo", 1000, Verdict.VALID, "test", null))
                .isInstanceOf(AccessDeniedException.class);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM review_finding_feedback", Long.class)).isEqualTo(1);
        assertThat(service.list("two", "repo", user(1))).isEmpty();
    }

    @Test void authenticatedHttpEndpointsValidateFeedbackAndDoNotTrustPayloadIdentity() throws Exception {
        var mvc = org.springframework.test.web.servlet.setup.MockMvcBuilders
                .standaloneSetup(new ReviewFeedbackController(service))
                .setControllerAdvice(new org.rostilos.codecrow.webserver.exception.GlobalExceptionHandler()).build();
        String base = "/api/one/project/repo/review-feedback";
        mvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put(base + "/issues/1000")
                        .principal(user(2)).contentType("application/json")
                        .content("{\"verdict\":\"VALID\",\"explanation\":\"Validated against source\",\"userId\":1}"))
                .andExpect(org.springframework.test.web.servlet.result.MockMvcResultMatchers.status().isOk())
                .andExpect(org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath("$.evidence[0].userId").value(2));
        String id = service.list("one", "repo", user(1)).get(0).id();
        mvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post(base + "/rules/" + id + "/promote")
                        .principal(user(2)).contentType("application/json").content("{\"userId\":1}"))
                .andExpect(org.springframework.test.web.servlet.result.MockMvcResultMatchers.status().isForbidden());
        mvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get(base + "/rules").principal(user(1)))
                .andExpect(org.springframework.test.web.servlet.result.MockMvcResultMatchers.status().isOk());
        mvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put(base + "/issues/1000")
                        .principal(user(2)).contentType("application/json").content("{\"verdict\":\"VALID\",\"explanation\":\"\"}"))
                .andExpect(org.springframework.test.web.servlet.result.MockMvcResultMatchers.status().isBadRequest());
        mvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put(base + "/issues/2000")
                        .principal(user(1)).contentType("application/json").content("{\"verdict\":\"VALID\",\"explanation\":\"Source\"}"))
                .andExpect(org.springframework.test.web.servlet.result.MockMvcResultMatchers.status().isNotFound());
        mvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put(base + "/issues/1000")
                        .contentType("application/json").content("{\"verdict\":\"VALID\",\"explanation\":\"Source\"}"))
                .andExpect(org.springframework.test.web.servlet.result.MockMvcResultMatchers.status().isForbidden());
        assertThat(jdbc.queryForObject("SELECT count(*) FROM review_finding_feedback", Long.class)).isEqualTo(1);
    }

    @Test void retriesAndRepeatedFindingsGroupWithoutInflatingIndependentEvidence() throws Exception {
        var rule = feedback(1000, Verdict.VALID, 2);
        assertThat(feedback(1000, Verdict.VALID, 2).evidence()).hasSize(1);
        var repeated = feedback(1001, Verdict.VALID, 2);
        assertThat(repeated.id()).isEqualTo(rule.id()); assertThat(repeated.distinctIssues()).isEqualTo(2);
        assertThat(repeated.validVotes()).isEqualTo(2);
        var pool = Executors.newFixedThreadPool(2);
        try {
            var tasks = List.<Callable<RuleView>>of(() -> feedback(1001, Verdict.FALSE_POSITIVE, 3),
                    () -> feedback(1001, Verdict.FALSE_POSITIVE, 3));
            for (var result : pool.invokeAll(tasks)) assertThat(result.get().evidence()).hasSize(3);
        } finally { pool.shutdownNow(); }
        assertThat(service.list("one", "repo", user(1))).singleElement()
                .satisfies(r -> { assertThat(r.evidence()).hasSize(3); assertThat(r.distinctIssues()).isEqualTo(2); });
    }

    @Test void existingRuleLimitRollsBackPromotionAndHistorySurvivesIssueCleanup() {
        var rule = feedback(1000, Verdict.VALID, 2);
        assertThatThrownBy(() -> service(1).promote("one", "repo", rule.id(), null, user(1)))
                .isInstanceOf(ResponseStatusException.class).hasMessageContaining("Maximum number");
        assertThat(service.list("one", "repo", user(1)).get(0).state()).isEqualTo(State.CANDIDATE);
        jdbc.update("DELETE FROM code_analysis_issue WHERE id=1000");
        assertThat(service.list("one", "repo", user(1)).get(0).evidence()).singleElement()
                .satisfies(e -> assertThat(e.issueId()).isEqualTo(1000));
    }

    @Test void learningStorageOutageCannotBlockReviewAndExistingSettingsCanDisableProjection() throws Exception {
        var rule = feedback(1000, Verdict.VALID, 2);
        service.promote("one", "repo", rule.id(), null, user(1));
        jdbc.execute("ALTER TABLE review_finding_feedback RENAME TO temporarily_unavailable");
        assertThat(requests(EVcsProvider.GITHUB).get(0).getProjectRules()).contains("learned:" + rule.id());
        jdbc.execute("ALTER TABLE temporarily_unavailable RENAME TO review_finding_feedback");
        jdbc.update("UPDATE project SET configuration=jsonb_set(configuration,'{projectRules,rules,1,enabled}','false') WHERE id=10");
        assertThat(service.list("one", "repo", user(1)).get(0).effective()).isFalse();
        assertOnlyManualRule();
    }

    @Test void feedbackWithControlCharactersRemainsValidJsonInTheReviewPayload() throws Exception {
        var rule = service.record("one", "repo", 1000, Verdict.VALID,
                "Source contract uses a form feed:\f and backspace:\b, with quotes: \"value\".", user(2));
        service.promote("one", "repo", rule.id(), null, user(1));
        String serialized = requests(EVcsProvider.GITHUB).get(0).getProjectRules();
        assertThat(mapper.readTree(serialized).get(1).path("description").asText()).contains("\f", "\b", "\"value\"");
    }

    private void assertOnlyManualRule() throws Exception {
        assertThat(mapper.readTree(requests(EVcsProvider.GITHUB).get(0).getProjectRules()).size()).isEqualTo(1);
        assertThat(jdbc.queryForObject("SELECT configuration->>'unknownSetting' FROM project WHERE id=10", String.class))
                .isEqualTo("preserved");
    }

    private List<AiAnalysisRequest> requests(EVcsProvider provider) throws Exception {
        Project project = mock(Project.class); VcsRepoInfo repository = mock(VcsRepoInfo.class);
        VcsConnection connection = mock(VcsConnection.class); AIConnection ai = mock(AIConnection.class);
        var binding = mock(ProjectAiConnectionBinding.class); Workspace workspace = mock(Workspace.class);
        var encryption = mock(TokenEncryptionService.class); var clients = mock(VcsClientProvider.class);
        VcsClient vcs = mock(VcsClient.class);
        when(project.getId()).thenReturn(10L); when(project.getNamespace()).thenReturn("repo");
        when(project.getWorkspace()).thenReturn(workspace); when(workspace.getName()).thenReturn("one");
        when(project.getEffectiveConfig()).thenReturn(mapper.readValue(jdbc.queryForObject(
                "SELECT configuration::text FROM project WHERE id=10", String.class), ProjectConfig.class));
        when(project.getEffectiveVcsRepoInfo()).thenReturn(repository);
        when(repository.getVcsConnection()).thenReturn(connection); when(repository.getRepoWorkspace()).thenReturn("owner");
        when(repository.getRepoSlug()).thenReturn("repo"); when(project.getAiBinding()).thenReturn(binding);
        when(binding.getAiConnection()).thenReturn(ai); when(ai.getProviderKey()).thenReturn(AIProviderKey.OPENAI);
        when(ai.getAiModel()).thenReturn("test-model"); when(ai.getApiKeyEncrypted()).thenReturn("encrypted-ai");
        when(encryption.decrypt(anyString())).thenReturn("test-token");
        when(connection.getProviderType()).thenReturn(provider);
        when(connection.getConnectionType()).thenReturn(EVcsConnectionType.ACCESS_TOKEN);
        when(connection.getAccessToken()).thenReturn("encrypted-vcs"); when(clients.getClient(connection)).thenReturn(vcs);
        when(vcs.getPullRequest("owner", "repo", 42L)).thenReturn(new VcsPullRequest(
                42L,"review","description","feature","main",BASE,BASE,HEAD,"OPEN",false,"url"));
        when(vcs.getCommitRangeDiff("owner", "repo", BASE, HEAD)).thenReturn(DIFF);
        AbstractVcsAiClientService builder = new AbstractVcsAiClientService(encryption, clients, null, null, null,
                null, new PullRequestDiffPreparationService(new AnalysisLimitEnforcer())) {
            @Override public EVcsProvider getProvider() { return provider; }
        };
        var pr = new PrProcessRequest(); pr.projectId=10L; pr.pullRequestId=42L; pr.commitHash=HEAD;
        pr.sourceBranchName="feature"; pr.targetBranchName="main"; pr.analysisType=AnalysisType.PR_REVIEW;
        var branch = new BranchProcessRequest(); branch.projectId=10L; branch.commitHash=HEAD;
        branch.targetBranchName="main"; branch.analysisType=AnalysisType.BRANCH_ANALYSIS;
        return List.of(builder.buildAiAnalysisRequests(project,pr,Optional.empty()).get(0),
                builder.buildDirectPushAnalysisRequests(project,branch,DIFF,Map.of(),List.of("src/App.java")).get(0));
    }
}
