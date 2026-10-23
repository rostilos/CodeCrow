package org.rostilos.codecrow.feedbacktests;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.persistence.EntityManager;
import org.junit.jupiter.api.*;
import org.rostilos.codecrow.core.model.codeanalysis.*;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.*;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.security.service.UserDetailsImpl;
import org.rostilos.codecrow.security.web.WorkspaceSecurity;
import org.rostilos.codecrow.testsupport.containers.SharedPostgresContainer;
import org.rostilos.codecrow.webserver.project.dto.request.UpdateProjectRulesRequest;
import org.rostilos.codecrow.webserver.project.service.ProjectService;
import org.rostilos.codecrow.webserver.reviewfeedback.ReviewFeedbackService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.SpringBootConfiguration;
import org.springframework.boot.autoconfigure.EnableAutoConfiguration;
import org.springframework.boot.autoconfigure.domain.EntityScan;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.annotation.Bean;
import org.springframework.data.jpa.repository.config.EnableJpaRepositories;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.Authentication;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.*;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@SpringBootTest(classes = ReviewFeedbackConfigurationIT.Config.class, properties = {
        "spring.flyway.enabled=false", "spring.jpa.hibernate.ddl-auto=create-drop",
        "spring.main.web-application-type=none", "spring.jpa.open-in-view=false",
        "spring.autoconfigure.exclude=org.rostilos.codecrow.email.config.EmailAutoConfiguration,org.rostilos.codecrow.publicshare.config.PublicShareAutoConfiguration,org.rostilos.codecrow.scmevidence.config.ScmEvidenceAutoConfiguration"
})
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class ReviewFeedbackConfigurationIT {
    private static final String SCHEMA = "feedback_jpa_" + UUID.randomUUID().toString().replace("-", "");
    @DynamicPropertySource static void database(DynamicPropertyRegistry registry) {
        var pg = SharedPostgresContainer.getInstance();
        new JdbcTemplate(new DriverManagerDataSource(pg.getJdbcUrl(), pg.getUsername(), pg.getPassword()))
                .execute("CREATE SCHEMA IF NOT EXISTS " + SCHEMA);
        registry.add("spring.datasource.url", () -> pg.getJdbcUrl() + (pg.getJdbcUrl().contains("?") ? "&" : "?") + "currentSchema=" + SCHEMA);
        registry.add("spring.datasource.username", pg::getUsername);
        registry.add("spring.datasource.password", pg::getPassword);
    }

    @SpringBootConfiguration @EnableAutoConfiguration
    @EntityScan("org.rostilos.codecrow.core.model")
    @EnableJpaRepositories(basePackageClasses = ProjectRepository.class)
    static class Config {
        @Bean WorkspaceSecurity workspaceSecurity() {
            var security = mock(WorkspaceSecurity.class);
            when(security.isWorkspaceProjectMember(anyString(), anyString(), any())).thenReturn(true);
            when(security.hasOwnerOrAdminRights(anyString(), any())).thenReturn(true);
            return security;
        }
        @Bean ReviewFeedbackService feedback(JdbcTemplate jdbc, WorkspaceSecurity security, ObjectMapper mapper,
                                             PlatformTransactionManager transactions) {
            return new ReviewFeedbackService(jdbc, security, mapper, transactions, 20);
        }
        @Bean ProjectService projectService(ProjectRepository projects, EntityManager em) throws Exception {
            var constructor = ProjectService.class.getConstructors()[0];
            Object[] args = java.util.Arrays.stream(constructor.getParameterTypes())
                    .map(type -> type == ProjectRepository.class ? projects : type == EntityManager.class ? em : mock(type))
                    .toArray();
            return (ProjectService) constructor.newInstance(args);
        }
    }

    @Autowired EntityManager em;
    @Autowired ProjectRepository projects;
    @Autowired ProjectService projectService;
    @Autowired ReviewFeedbackService feedback;
    @Autowired JdbcTemplate jdbc;
    @Autowired PlatformTransactionManager transactionManager;
    private TransactionTemplate transactions;
    private Long workspaceId, projectId, issueId;
    private String workspaceSlug, ruleId;
    private final Authentication owner = UsernamePasswordAuthenticationToken.authenticated(
            new UserDetailsImpl(1L,"owner","owner@example.invalid","",null,List.of()), "", List.of());

    @BeforeAll void migrate() throws Exception {
        try (var source = getClass().getResourceAsStream("/db/migration/managed/V2.34.0__review_feedback_learning.sql")) {
            assertThat(source).isNotNull(); jdbc.execute(new String(source.readAllBytes(), StandardCharsets.UTF_8));
        }
    }
    @BeforeEach void fixture() {
        transactions = new TransactionTemplate(transactionManager);
        transactions.executeWithoutResult(status -> {
            workspaceSlug = UUID.randomUUID().toString();
            var workspace = new Workspace(workspaceSlug, workspaceSlug, "test"); em.persist(workspace);
            var project = new Project(); project.setWorkspace(workspace); project.setNamespace("repo");
            var config = new ProjectConfig(); config.setMainBranch("main");
            config.setProjectRules(new ProjectRulesConfig(List.of(new ProjectRulesConfig.CustomRule(
                    "manual", "Manual", "Preserve manual instruction", RuleType.ENFORCE, List.of(), true, 0))));
            project.setConfiguration(config); em.persist(project);
            var analysis = new CodeAnalysis(); analysis.setProject(project); analysis.setAnalysisType(AnalysisType.PR_REVIEW);
            analysis.setCommitHash("a".repeat(40)); em.persist(analysis);
            var issue = new CodeAnalysisIssue(); issue.setAnalysis(analysis); issue.setTitle("Missing request validation");
            issue.setFilePath("src/App.java"); issue.setSeverity(IssueSeverity.HIGH); em.persist(issue); em.flush();
            workspaceId = workspace.getId(); projectId = project.getId(); issueId = issue.getId();
        });
        ruleId = feedback.record(workspaceSlug,"repo",issueId,ReviewFeedbackService.Verdict.VALID,
                "The exposed entry point accepts unvalidated requests",owner).id();
    }

    @Test void configurationUpdateRefreshesEntityLoadedBeforeConcurrentRulePromotion() throws Exception {
        var loaded = new CountDownLatch(1); var promoted = new CountDownLatch(1);
        var pool = Executors.newSingleThreadExecutor();
        try {
            Future<?> change = pool.submit(() -> transactions.executeWithoutResult(status -> {
                em.find(Project.class, projectId); // Simulate a cached entity from authorization earlier in the request.
                loaded.countDown(); await(promoted);
                projectService.updateAnalysisSettings(workspaceId,projectId,null,null,null,null,false,null);
            }));
            assertThat(loaded.await(10,TimeUnit.SECONDS)).isTrue();
            feedback.promote(workspaceSlug,"repo",ruleId,null,owner); promoted.countDown(); change.get(15,TimeUnit.SECONDS);
            transactions.executeWithoutResult(status -> {
                var reloaded = projects.findById(projectId).orElseThrow();
                assertThat(reloaded.getEffectiveConfig().useMcpTools()).isFalse();
                assertThat(reloaded.getEffectiveConfig().mainBranch()).isEqualTo("main");
                assertThat(reloaded.getEffectiveConfig().getProjectRulesConfig().rules())
                        .extracting(ProjectRulesConfig.CustomRule::id).containsExactly("manual","learned:"+ruleId);
            });
        } finally { promoted.countDown(); pool.shutdownNow(); }
    }

    @Test void promotionWaitsForManualEditAndPreservesItsCommittedRules() throws Exception {
        var edited = new CountDownLatch(1); var release = new CountDownLatch(1);
        var pool = Executors.newFixedThreadPool(2);
        try {
            var change = pool.submit(() -> transactions.executeWithoutResult(status -> {
                projectService.updateProjectRules(workspaceId,projectId,new UpdateProjectRulesRequest(List.of(
                        new UpdateProjectRulesRequest.CustomRuleRequest("manual-new","New manual", "Owner edit",
                                RuleType.ENFORCE,List.of(),true,0))));
                edited.countDown(); await(release);
            }));
            assertThat(edited.await(10,TimeUnit.SECONDS)).isTrue();
            var promotion = pool.submit(() -> feedback.promote(workspaceSlug,"repo",ruleId,null,owner));
            release.countDown(); change.get(15,TimeUnit.SECONDS); assertThat(promotion.get(15,TimeUnit.SECONDS).effective()).isTrue();
            transactions.executeWithoutResult(status -> assertThat(projects.findById(projectId).orElseThrow()
                    .getEffectiveConfig().getProjectRulesConfig().rules()).extracting(ProjectRulesConfig.CustomRule::id)
                    .containsExactly("manual-new","learned:"+ruleId));
        } finally { release.countDown(); pool.shutdownNow(); }
    }

    @Test void unrelatedProjectUpdateDoesNotWriteItsStaleConfigurationColumn() throws Exception {
        var loaded = new CountDownLatch(1); var promoted = new CountDownLatch(1);
        var pool = Executors.newSingleThreadExecutor();
        try {
            var rename = pool.submit(() -> transactions.executeWithoutResult(status -> {
                var cached = em.find(Project.class,projectId); loaded.countDown(); await(promoted);
                cached.setName("Renamed concurrently"); em.flush();
            }));
            assertThat(loaded.await(10,TimeUnit.SECONDS)).isTrue();
            feedback.promote(workspaceSlug,"repo",ruleId,null,owner); promoted.countDown(); rename.get(15,TimeUnit.SECONDS);
            transactions.executeWithoutResult(status -> {
                var project = projects.findById(projectId).orElseThrow();
                assertThat(project.getName()).isEqualTo("Renamed concurrently");
                assertThat(project.getEffectiveConfig().getProjectRulesConfig().rules())
                        .extracting(ProjectRulesConfig.CustomRule::id).contains("learned:"+ruleId);
            });
        } finally { promoted.countDown(); pool.shutdownNow(); }
    }

    private static void await(CountDownLatch latch) {
        try { if (!latch.await(10,TimeUnit.SECONDS)) throw new AssertionError("Concurrent transaction did not reach checkpoint"); }
        catch (InterruptedException e) { Thread.currentThread().interrupt(); throw new IllegalStateException(e); }
    }
}
