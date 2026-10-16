package org.rostilos.codecrow.analysisengine;

import jakarta.persistence.EntityManager;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.analysisengine.service.AstScopeEnricher;
import org.rostilos.codecrow.analysisengine.service.IssueReconciliationEngine;
import org.rostilos.codecrow.analysisengine.service.pr.PrIssueTrackingService;
import org.rostilos.codecrow.core.model.codeanalysis.*;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.*;
import org.rostilos.codecrow.testsupport.base.IntegrationTest;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.SpringBootConfiguration;
import org.springframework.boot.autoconfigure.EnableAutoConfiguration;
import org.springframework.boot.autoconfigure.domain.EntityScan;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.annotation.Bean;
import org.springframework.data.jpa.repository.config.EnableJpaRepositories;
import org.springframework.test.context.transaction.TestTransaction;
import org.springframework.transaction.annotation.Transactional;
import java.util.Map;
import java.util.UUID;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;

@IntegrationTest
@SpringBootTest(classes=PrTrackingPersistenceIT.Config.class,
    properties={"spring.flyway.enabled=false", "spring.jpa.hibernate.ddl-auto=create-drop"})
class PrTrackingPersistenceIT {
    @SpringBootConfiguration @EnableAutoConfiguration
    @EntityScan("org.rostilos.codecrow.core.model")
    @EnableJpaRepositories(basePackageClasses={CodeAnalysisRepository.class,
            org.rostilos.codecrow.core.persistence.repository.branch.BranchIssueRepository.class,
            org.rostilos.codecrow.core.persistence.repository.qualitygate.QualityGateRepository.class})
    static class Config {
        @Bean org.rostilos.codecrow.core.service.CodeAnalysisService analysisService(CodeAnalysisIssueRepository issues, CodeAnalysisRepository analyses,
                org.rostilos.codecrow.core.persistence.repository.qualitygate.QualityGateRepository gates) {
            return new org.rostilos.codecrow.core.service.CodeAnalysisService(analyses, issues, gates,
                new org.rostilos.codecrow.core.service.qualitygate.QualityGateEvaluator(gates),
                new org.rostilos.codecrow.core.service.IssueDeduplicationService(),
                new org.rostilos.codecrow.core.service.PrChangeSummaryService(analyses, new com.fasterxml.jackson.databind.ObjectMapper()));
        }
        @Bean PrIssueTrackingService tracking(CodeAnalysisIssueRepository issues,
                org.rostilos.codecrow.core.service.CodeAnalysisService analysisService) {
            return new PrIssueTrackingService(issues, mock(IssueReconciliationEngine.class), new AstScopeEnricher(), analysisService);
        }
    }
    @Autowired EntityManager em;
    @Autowired CodeAnalysisRepository analyses;
    @Autowired PrIssueTrackingService tracking;
    @Autowired org.rostilos.codecrow.core.service.CodeAnalysisService analysisService;
    @Autowired javax.sql.DataSource dataSource;
    @Autowired CodeAnalysisIssueRepository analysisIssues;
    @Autowired org.rostilos.codecrow.core.persistence.repository.branch.BranchIssueRepository branchIssues;

    @Test @Transactional
    void removalImpactSurvivesBranchImportAndReloadWithMissingHeadPath() {
        String name = UUID.randomUUID().toString();
        var workspace = new Workspace(name, name, "removal test"); em.persist(workspace);
        var project = new Project(); project.setWorkspace(workspace); project.setNamespace(name); em.persist(project);
        var analysis = new CodeAnalysis(); analysis.setProject(project); analysis.setAnalysisType(AnalysisType.PR_REVIEW);
        analysis.setPrNumber(9L); analysis.setCommitHash("head"); analysis.setPrVersion(1); em.persist(analysis);
        var issue = new CodeAnalysisIssue(); issue.setAnalysis(analysis); issue.setFilePath("deleted.py");
        issue.setSeverity(IssueSeverity.HIGH); issue.setAnchorSide(IssueAnchorSide.BASE);
        issue.setReason("Deleting the authorization guard leaves the surviving caller unprotected.");
        em.persist(issue);
        var branch = new org.rostilos.codecrow.core.model.branch.Branch(); branch.setProject(project);
        branch.setBranchName("main"); em.persist(branch); em.flush();
        new org.rostilos.codecrow.analysisengine.service.branch.BranchIssueMappingService(analysisIssues, branchIssues)
                .mapCodeAnalysisIssuesToBranch(java.util.Set.of("deleted.py"), java.util.Set.of(), branch, project, 9L);
        em.flush(); TestTransaction.flagForCommit(); TestTransaction.end(); TestTransaction.start();
        var imported = branchIssues.findByBranchIdAndOriginIssueId(branch.getId(), issue.getId()).orElseThrow();
        assertThat(imported.getAnchorSide()).isEqualTo(IssueAnchorSide.BASE);
        assertThat(imported.isResolved()).isFalse();
    }

    @Test
    void summaryMigrationPreservesEachAnalysisPayload() throws Exception {
        String schema = "atomic_summary_" + UUID.randomUUID().toString().replace("-", "");
        try (var connection = dataSource.getConnection(); var sql = connection.createStatement()) {
            sql.execute("CREATE SCHEMA " + schema);
            try {
                connection.setSchema(schema);
                sql.execute("CREATE TABLE code_analysis (id bigint PRIMARY KEY)");
                sql.execute("CREATE TABLE pr_change_summary (analysis_id bigint PRIMARY KEY, payload text)");
                sql.execute("INSERT INTO code_analysis VALUES (1),(2),(3)");
                sql.execute("INSERT INTO pr_change_summary VALUES (1,'{\"baseRevision\":\"one\"}'),(2,'{\"baseRevision\":\"two\"}')");
                try (var migration = getClass().getResourceAsStream("/db/migration/managed/V2.38.0__atomic_pr_change_summary.sql")) {
                    assertThat(migration).isNotNull();
                    sql.execute(new String(migration.readAllBytes(), java.nio.charset.StandardCharsets.UTF_8));
                }
                try (var rows = sql.executeQuery("SELECT pr_change_summary_payload FROM code_analysis ORDER BY id")) {
                    rows.next(); assertThat(rows.getString(1)).isEqualTo("{\"baseRevision\":\"one\"}");
                    rows.next(); assertThat(rows.getString(1)).isEqualTo("{\"baseRevision\":\"two\"}");
                    rows.next(); assertThat(rows.getString(1)).isNull();
                }
            } finally { connection.setSchema("public"); sql.execute("DROP SCHEMA " + schema + " CASCADE"); }
        }
    }

    @Test @Transactional
    void summaryCommitsWithItsExactAnalysisAndRollsBackWithIt() {
        String name = UUID.randomUUID().toString();
        var workspace = new Workspace(name, name, "summary test"); em.persist(workspace);
        var project = new Project(); project.setWorkspace(workspace); project.setNamespace(name); em.persist(project);
        em.flush();
        Map<String,Object> summary = Map.of("projectId", project.getId(), "pullRequestId", 9L,
                "sourceRevision", "head", "baseRevision", "base-one", "files", java.util.List.of(Map.of("path", "a.py")));
        var saved = analysisService.createAnalysisFromAiResponse(project,
                Map.of("comment", "Review", "prChangeSummary", summary), 9L, "main", "feature", "head", null, null, "review-one");
        em.flush(); TestTransaction.flagForCommit(); TestTransaction.end(); TestTransaction.start();
        String stored = (String) em.createNativeQuery("SELECT pr_change_summary_payload FROM code_analysis WHERE id=:id")
                .setParameter("id", saved.getId()).getSingleResult();
        assertThat(stored).contains("base-one", "a.py");
        var rolledBack = analysisService.createAnalysisFromAiResponse(project,
                Map.of("comment", "Review", "prChangeSummary", summary), 9L, "main", "feature", "head", null, null, "review-two");
        em.flush(); TestTransaction.flagForRollback(); TestTransaction.end(); TestTransaction.start();
        assertThat(analyses.findById(rolledBack.getId())).isEmpty();
        assertThat(em.createNativeQuery("SELECT pr_change_summary_payload FROM code_analysis WHERE id=:id")
                .setParameter("id", saved.getId()).getSingleResult()).isEqualTo(stored);
        var degraded = analysisService.createAnalysisFromAiResponse(project,
                Map.of("comment", "Review", "prChangeSummary", Map.of("projectId", -1L)),
                9L, "main", "feature", "new-head", null, null, "degraded");
        em.flush(); TestTransaction.flagForCommit(); TestTransaction.end(); TestTransaction.start();
        assertThat(analyses.findById(degraded.getId()).orElseThrow().getPrChangeSummaryPayload()).isNull();
    }

    @Test
    void manualDecisionMigrationBackfillsRecordedClosuresAndInitializesConcurrencyVersion() throws Exception {
        String schema = "manual_decision_" + UUID.randomUUID().toString().replace("-", "");
        try (var connection = dataSource.getConnection(); var sql = connection.createStatement()) {
            sql.execute("CREATE SCHEMA " + schema);
            try {
                connection.setSchema(schema);
                sql.execute("CREATE TABLE code_analysis_issue (id bigint PRIMARY KEY, is_resolved boolean, resolved_by text, resolved_at timestamptz, created_at timestamptz)");
                sql.execute("INSERT INTO code_analysis_issue VALUES (1,true,'manual','2026-01-01','2025-01-01'),(2,true,'pr-tracking','2026-01-01','2025-01-01'),(3,false,null,null,'2025-01-01')");
                try (var migration = getClass().getResourceAsStream("/db/migration/managed/V2.37.0__manual_pr_issue_decisions.sql")) {
                    assertThat(migration).isNotNull();
                    sql.execute(new String(migration.readAllBytes(), java.nio.charset.StandardCharsets.UTF_8));
                }
                try (var rows = sql.executeQuery("SELECT id, manual_status_resolved, manual_status_updated_at=resolved_at AS preserved_time, row_version FROM code_analysis_issue ORDER BY id")) {
                    rows.next(); assertThat(rows.getBoolean(2)).isTrue(); assertThat(rows.getBoolean(3)).isTrue(); assertThat(rows.getLong(4)).isZero();
                    rows.next(); assertThat(rows.getObject(2)).isNull(); assertThat(rows.getLong(4)).isZero();
                    rows.next(); assertThat(rows.getObject(2)).isNull();
                }
            } finally { connection.setSchema("public"); sql.execute("DROP SCHEMA " + schema + " CASCADE"); }
        }
    }

    @Test
    void iterationMigrationPreservesOldRowsAndAllowsAnotherReviewAtSameCommit() throws Exception {
        String schema = "review_identity_" + UUID.randomUUID().toString().replace("-", "");
        try (var connection = dataSource.getConnection(); var sql = connection.createStatement()) {
            sql.execute("CREATE SCHEMA " + schema);
            try {
                connection.setSchema(schema);
                sql.execute("CREATE TABLE code_analysis (id bigint PRIMARY KEY, project_id bigint, pr_number bigint, commit_hash text, pr_version integer, CONSTRAINT uq_code_analysis_project_commit UNIQUE(project_id,commit_hash,pr_number))");
                sql.execute("INSERT INTO code_analysis VALUES (1,7,9,'head',1)");
                try (var migration = getClass().getResourceAsStream("/db/migration/managed/V2.36.0__pr_review_iteration_identity.sql")) {
                    assertThat(migration).isNotNull();
                    sql.execute(new String(migration.readAllBytes(), java.nio.charset.StandardCharsets.UTF_8));
                }
                sql.execute("INSERT INTO code_analysis VALUES (2,7,9,'head',2)");
                try (var rows = sql.executeQuery("SELECT count(*) FROM code_analysis")) {
                    rows.next(); assertThat(rows.getInt(1)).isEqualTo(2);
                }
            } finally { connection.setSchema("public"); sql.execute("DROP SCHEMA " + schema + " CASCADE"); }
        }
    }

    @Test @Transactional
    void staleAnalysisSaveCannotOverwriteConcurrentManualDecision() {
        String name = UUID.randomUUID().toString();
        var workspace = new Workspace(name, name, "test"); em.persist(workspace);
        var project = new Project(); project.setWorkspace(workspace); project.setNamespace(name); em.persist(project);
        var stale = analysis(project, 1, false);
        em.flush(); TestTransaction.flagForCommit(); TestTransaction.end(); TestTransaction.start();
        var fresh = analyses.findById(stale.getId()).orElseThrow();
        fresh.getIssues().get(0).setResolved(true);
        fresh.getIssues().get(0).setResolvedBy("manual");
        fresh.getIssues().get(0).setResolvedAt(java.time.OffsetDateTime.now());
        analysisService.saveAnalysis(fresh);
        TestTransaction.flagForCommit(); TestTransaction.end();

        assertThatThrownBy(() -> analysisService.saveAnalysis(stale))
                .isInstanceOf(org.springframework.orm.ObjectOptimisticLockingFailureException.class);
        TestTransaction.start();
        var retained = analyses.findById(stale.getId()).orElseThrow();
        assertThat(retained.getIssues().get(0).isResolved()).isTrue();
        assertThat(retained.getTotalIssues()).isZero();
    }

    @Test @Transactional
    void manualResolutionSurvivesEmptyIterationAndNoIdRediscoveryWithinOwnPr() {
        String name = UUID.randomUUID().toString();
        var workspace = new Workspace(name, name, "test"); em.persist(workspace);
        var project = new Project(); project.setWorkspace(workspace); project.setNamespace(name); em.persist(project);
        Map<String, Object> issue = Map.of("file", "a.py", "line", 2, "severity", "HIGH",
                "category", "BUG_RISK", "title", "Risky call", "codeSnippet", "risky()",
                "reason", "The unchecked input reaches the risky call and can invalidate persisted data.");
        Map<String, Object> response = Map.of("comment", "Review", "issues", java.util.List.of(issue));
        var old = analysisService.createAnalysisFromAiResponse(project, response, 9L,
                "main", "feature", "head-1", "actor", "actor", "review-1", Map.of("a.py", "x\nrisky()\n"));
        var decision = old.getIssues().get(0); decision.setResolved(true); decision.setResolvedBy("manual");
        decision.setResolvedAt(java.time.OffsetDateTime.now()); decision.setResolvedDescription("Accepted risk");
        analysisService.saveAnalysis(old);
        analysisService.createAnalysisFromAiResponse(project, Map.of("comment", "Empty", "issues", java.util.List.of()),
                9L, "main", "feature", "head-2", "actor", "actor", "review-2");
        em.flush(); TestTransaction.flagForCommit(); TestTransaction.end(); TestTransaction.start();

        var rediscovered = analysisService.createAnalysisFromAiResponse(project, response, 9L,
                "main", "feature", "head-3", "actor", "actor", "review-3", Map.of("a.py", "// shifted\nx\nrisky()\n"));
        em.flush(); em.clear();
        var persisted = analyses.findById(rediscovered.getId()).orElseThrow();
        assertThat(persisted.getIssues().get(0).isResolved()).isTrue();
        assertThat(persisted.getTotalIssues()).isZero();
        var anotherPr = analysisService.createAnalysisFromAiResponse(project, response, 10L,
                "main", "other", "head-3", "actor", "actor", "review-other", Map.of("a.py", "x\nrisky()\n"));
        assertThat(anotherPr.getIssues().get(0).isResolved()).isFalse();

        var reopened = analyses.findById(old.getId()).orElseThrow().getIssues().get(0);
        reopened.setResolved(false); reopened.setResolvedBy(null); reopened.setResolvedAt(null);
        org.springframework.test.util.ReflectionTestUtils.setField(reopened, "manualStatusResolved", false);
        org.springframework.test.util.ReflectionTestUtils.setField(reopened, "manualStatusUpdatedAt", java.time.OffsetDateTime.now());
        analysisService.saveAnalysis(reopened.getAnalysis());
        var afterReopen = analysisService.createAnalysisFromAiResponse(project, response, 9L,
                "main", "feature", "head-4", "actor", "actor", "review-4", Map.of("a.py", "x\nrisky()\n"));
        assertThat(afterReopen.getIssues().get(0).isResolved()).isFalse();
    }

    @Test @Transactional
    void sameHeadWithChangedReviewIdentityKeepsBothIterationsAndReturnsLatestCache() {
        String name = UUID.randomUUID().toString();
        var workspace = new Workspace(name, name, "test"); em.persist(workspace);
        var project = new Project(); project.setWorkspace(workspace); project.setNamespace(name); em.persist(project);
        var first = analysisService.createAnalysisFromAiResponse(project, Map.of("comment", "old", "issues", java.util.List.of()),
                9L, "main", "feature", "same-head", "actor", "actor", "old-identity");
        var second = analysisService.createAnalysisFromAiResponse(project, Map.of("comment", "new", "issues", java.util.List.of()),
                9L, "main", "feature", "same-head", "actor", "actor", "new-identity");
        em.flush(); TestTransaction.flagForCommit(); TestTransaction.end(); TestTransaction.start();
        assertThat(second.getId()).isNotEqualTo(first.getId());
        assertThat(analyses.findById(first.getId()).orElseThrow().getComment()).isEqualTo("old");
        assertThat(analysisService.getCodeAnalysisCache(project.getId(), "same-head", 9L)).get()
                .extracting(CodeAnalysis::getComment).isEqualTo("new");
    }

    @Test @Transactional
    void resolutionAndParentCountsSurviveTransactionAndReload() {
        String name = UUID.randomUUID().toString();
        var workspace = new Workspace(name, name, "test"); em.persist(workspace);
        var project = new Project(); project.setWorkspace(workspace); project.setNamespace(name); em.persist(project);
        var previous = analysis(project, 1, true);
        var current = analysis(project, 2, false);
        em.flush(); TestTransaction.flagForCommit(); TestTransaction.end();

        tracking.trackPrIteration(current, previous, Map.of("a.py", "risky()\n"), Map.of("a.py", "risky()\n"));

        TestTransaction.start();
        var reloaded = analyses.findById(current.getId()).orElseThrow();
        assertThat(reloaded.getIssues().get(0).isResolved()).isTrue();
        assertThat(reloaded.getTotalIssues()).isZero();
        assertThat(reloaded.getResolvedCount()).isEqualTo(1);
    }

    private CodeAnalysis analysis(Project project, int version, boolean resolved) {
        var analysis = new CodeAnalysis(); analysis.setProject(project); analysis.setPrNumber(9L);
        analysis.setAnalysisType(AnalysisType.PR_REVIEW); analysis.setCommitHash("head-" + version);
        analysis.setPrVersion(version);
        var issue = new CodeAnalysisIssue(); issue.setFilePath("a.py"); issue.setLineNumber(1);
        issue.setTitle("Risky call"); issue.setReason("Risky call"); issue.setSeverity(IssueSeverity.HIGH);
        issue.setIssueFingerprint("same-fingerprint"); issue.setResolved(resolved);
        analysis.addIssue(issue); em.persist(analysis); return analysis;
    }
}
