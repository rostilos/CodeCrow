package org.rostilos.codecrow.core.persistence;

import jakarta.persistence.EntityManager;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.SpringBootConfiguration;
import org.springframework.boot.autoconfigure.EnableAutoConfiguration;
import org.springframework.boot.autoconfigure.domain.EntityScan;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.jpa.repository.config.EnableJpaRepositories;
import org.springframework.transaction.annotation.Transactional;
import org.rostilos.codecrow.core.model.job.*;
import org.rostilos.codecrow.core.model.codeanalysis.*;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisIssueRepository;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.model.vcs.*;
import org.rostilos.codecrow.core.persistence.repository.job.JobRepository;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsRepoBindingRepository;
import org.rostilos.codecrow.testsupport.annotation.IntegrationTest;
import java.time.OffsetDateTime;
import java.util.UUID;
import javax.sql.DataSource;
import static org.assertj.core.api.Assertions.*;

@IntegrationTest
@SpringBootTest(classes=RepositoryBoundaryIT.Config.class,
        properties={"spring.flyway.enabled=false", "spring.jpa.hibernate.ddl-auto=create-drop"})
class RepositoryBoundaryIT {
    @SpringBootConfiguration @EnableAutoConfiguration
    @EntityScan("org.rostilos.codecrow.core.model")
    @EnableJpaRepositories(basePackageClasses={JobRepository.class, VcsRepoBindingRepository.class, CodeAnalysisIssueRepository.class})
    static class Config {}
    @Autowired EntityManager em;
    @Autowired JobRepository jobs;
    @Autowired VcsRepoBindingRepository bindings;
    @Autowired DataSource dataSource;
    @ParameterizedTest
    @ValueSource(booleans = {false, true})
    void rowVersionRepairPreservesPopulatedTablesAndExistingCounters(boolean existingNullableColumn) throws Exception {
        String schema = "version_repair_" + UUID.randomUUID().toString().replace("-", "");
        try (var conn = dataSource.getConnection(); var sql = conn.createStatement();
             var source = getClass().getResourceAsStream("/db/migration/managed/V2.44.0__restore_issue_and_qa_row_versions.sql")) {
            assertThat(source).isNotNull();
            String migration = new String(source.readAllBytes(), java.nio.charset.StandardCharsets.UTF_8);
            sql.execute("CREATE SCHEMA " + schema);
            try {
                conn.setSchema(schema);
                for (String table : java.util.List.of("code_analysis_issue", "qa_doc_document")) {
                    sql.execute("CREATE TABLE " + table + " (id bigint PRIMARY KEY, payload text"
                            + (existingNullableColumn ? ", row_version bigint" : "") + ")");
                    sql.execute("INSERT INTO " + table + " (id,payload) VALUES (1,'saved content'),(2,'another record')");
                    if (existingNullableColumn) sql.execute("UPDATE " + table + " SET row_version=7 WHERE id=2");
                }
                sql.execute(migration);
                for (String table : java.util.List.of("code_analysis_issue", "qa_doc_document")) {
                    try (var rows = sql.executeQuery("SELECT payload,row_version FROM " + table + " ORDER BY id")) {
                        assertThat(rows.next()).isTrue();
                        assertThat(rows.getString(1)).isEqualTo("saved content");
                        assertThat(rows.getLong(2)).isZero();
                        assertThat(rows.next()).isTrue();
                        assertThat(rows.getString(1)).isEqualTo("another record");
                        assertThat(rows.getLong(2)).isEqualTo(existingNullableColumn ? 7 : 0);
                        assertThat(rows.next()).isFalse();
                    }
                    sql.execute("UPDATE " + table + " SET row_version=11 WHERE id=2");
                }
                // A normal upgrade after an immediate repair must retain concurrent-write protection.
                sql.execute(migration);
                for (String table : java.util.List.of("code_analysis_issue", "qa_doc_document")) {
                    sql.execute("INSERT INTO " + table + " (id,payload) VALUES (3,'new record')");
                    try (var rows = sql.executeQuery("SELECT row_version FROM " + table + " ORDER BY id")) {
                        assertThat(rows.next()).isTrue(); assertThat(rows.getLong(1)).isZero();
                        assertThat(rows.next()).isTrue(); assertThat(rows.getLong(1)).isEqualTo(11);
                        assertThat(rows.next()).isTrue(); assertThat(rows.getLong(1)).isZero();
                        assertThat(rows.next()).isFalse();
                    }
                    assertThatThrownBy(() -> sql.execute("UPDATE " + table + " SET row_version=NULL WHERE id=1"))
                            .isInstanceOf(java.sql.SQLException.class).hasMessageContaining("not-null constraint");
                }
            } finally { conn.setSchema("public"); sql.execute("DROP SCHEMA " + schema + " CASCADE"); }
        }
    }
    @Test void durableQaAndBranchMigrationsPreserveExistingRowsAndRecoveryDefaults() throws Exception {
        String schema = "recovery_test_" + UUID.randomUUID().toString().replace("-", "");
        try (var conn = dataSource.getConnection(); var sql = conn.createStatement()) {
            sql.execute("CREATE SCHEMA " + schema);
            try {
                conn.setSchema(schema);
                sql.execute("CREATE TABLE code_analysis (id bigint PRIMARY KEY); INSERT INTO code_analysis VALUES (1)");
                sql.execute("CREATE TABLE job (id bigint PRIMARY KEY, job_type text, trigger_source text); INSERT INTO job VALUES (1,'QA_DOC_COMMAND','CHAINED')");
                sql.execute("CREATE TABLE branch (id bigint PRIMARY KEY); INSERT INTO branch VALUES (1)");
                sql.execute("CREATE TABLE branch_issue (id bigint PRIMARY KEY); INSERT INTO branch_issue VALUES (1)");
                for (String migration : java.util.List.of("V2.40.0__durable_qa_jobs.sql", "V2.41.0__branch_reconciliation_state.sql")) {
                    try (var source = getClass().getResourceAsStream("/db/migration/managed/" + migration)) {
                        assertThat(source).isNotNull();
                        sql.execute(new String(source.readAllBytes(), java.nio.charset.StandardCharsets.UTF_8));
                    }
                }
                try (var rows = sql.executeQuery("SELECT qa_documentation_pending FROM code_analysis WHERE id=1")) {
                    assertThat(rows.next()).isTrue(); assertThat(rows.getBoolean(1)).isFalse();
                }
                try (var rows = sql.executeQuery("SELECT reconciliation_pending, reconciliation_attempts, row_version FROM branch WHERE id=1")) {
                    assertThat(rows.next()).isTrue(); assertThat(rows.getBoolean(1)).isFalse();
                    assertThat(rows.getInt(2)).isZero(); assertThat(rows.getLong(3)).isZero();
                }
                try (var rows = sql.executeQuery("SELECT row_version FROM branch_issue WHERE id=1")) {
                    assertThat(rows.next()).isTrue(); assertThat(rows.getLong(1)).isZero();
                }
                sql.execute("UPDATE job SET input_analysis_id=1 WHERE id=1");
                assertThatThrownBy(() -> sql.execute("INSERT INTO job VALUES (2,'QA_DOC_COMMAND','CHAINED',1)"))
                        .isInstanceOf(java.sql.SQLException.class).hasMessageContaining("idx_job_qa_input");
                sql.execute("DELETE FROM code_analysis WHERE id=1");
                try (var rows = sql.executeQuery("SELECT input_analysis_id FROM job WHERE id=1")) {
                    assertThat(rows.next()).isTrue(); assertThat(rows.getObject(1)).isNull();
                }
            } finally { conn.setSchema("public"); sql.execute("DROP SCHEMA " + schema + " CASCADE"); }
        }
    }
    @Autowired CodeAnalysisIssueRepository issues;

    VcsRepoBinding binding(String repoId, String slug, String namespace, Workspace workspace) {
        var connection = new VcsConnection(); connection.setWorkspace(workspace);
        connection.setConnectionName("server-" + UUID.randomUUID()); connection.setProviderType(EVcsProvider.GITLAB);
        em.persist(connection);
        var project = new Project(); project.setWorkspace(workspace); project.setNamespace(UUID.randomUUID().toString());
        em.persist(project);
        var binding = new VcsRepoBinding(); binding.setWorkspace(workspace); binding.setProject(project);
        binding.setVcsConnection(connection); binding.setProvider(EVcsProvider.GITLAB);
        binding.setExternalRepoId(repoId); binding.setExternalRepoSlug(slug); binding.setExternalNamespace(namespace);
        em.persist(binding); em.flush(); return binding;
    }
    Workspace workspace() {
        String id = UUID.randomUUID().toString(); var w = new Workspace(id, id, "test"); em.persist(w); return w;
    }
    @Test @Transactional void bindingsScopeIdentityAndLegacyFallbackToNamespace() {
        var first = binding("42", "repo", "one", workspace());
        var second = binding("42", "repo", "two", workspace());
        var third = binding("42", "repo", "three", first.getWorkspace());
        var legacy = binding("repo", "repo", "legacy-owner", workspace());
        assertThat(bindings.findWebhookCandidates(EVcsProvider.GITLAB,"42","repo","one"))
                .extracting(VcsRepoBinding::getId).containsExactlyInAnyOrder(first.getId(),second.getId(),third.getId());
        assertThat(bindings.findWebhookCandidates(EVcsProvider.GITLAB,"unmatched","repo","wrong-owner")).isEmpty();
        assertThat(bindings.findWebhookCandidates(EVcsProvider.GITLAB,"unmatched","repo","legacy-owner"))
                .extracting(VcsRepoBinding::getId).containsExactly(legacy.getId());
        assertThat(bindings.existsByWorkspace_IdAndVcsConnection_IdAndExternalRepoId(
                first.getWorkspace().getId(),first.getVcsConnection().getId(),"42")).isTrue();
        assertThat(bindings.existsByWorkspace_IdAndVcsConnection_IdAndExternalRepoId(
                second.getWorkspace().getId(),first.getVcsConnection().getId(),"42")).isFalse();
    }
    @Test @Transactional void queuedPipelineWorkHasOneAtomicExecutionClaimAndCanRecoverAfterInactivity() {
        var binding = binding("42","repo","one",workspace());
        var job = new Job(); job.setProject(binding.getProject()); job.setJobType(JobType.PR_ANALYSIS);
        job.setTriggerSource(JobTriggerSource.PIPELINE); job.setStatus(JobStatus.QUEUED);
        em.persist(job); em.flush(); Long id=job.getId();
        em.createNativeQuery("UPDATE job SET updated_at = :old WHERE id = :id")
                .setParameter("old",OffsetDateTime.now().minusHours(1)).setParameter("id",id).executeUpdate();
        assertThat(jobs.findRecoverableWebhookJobs(OffsetDateTime.now().minusSeconds(30),PageRequest.of(0,50)))
                .extracting(Job::getId).contains(id);
        assertThat(jobs.claimPipelineJobForExecution(id,OffsetDateTime.now())).isEqualTo(1);
        assertThat(jobs.claimPipelineJobForExecution(id,OffsetDateTime.now())).isZero();
        assertThat(jobs.findRecoverableWebhookJobs(OffsetDateTime.now(),PageRequest.of(0,50))).isEmpty();
        em.createNativeQuery("UPDATE job SET updated_at = :old WHERE id = :id")
                .setParameter("old",OffsetDateTime.now().minusHours(1)).setParameter("id",id).executeUpdate();
        assertThat(jobs.findAbandonedRunningWebhookJobs(OffsetDateTime.now().minusMinutes(30),PageRequest.of(0,20)))
                .extracting(Job::getId).contains(id);
        assertThat(jobs.claimAbandonedRunningWebhookJob(id,OffsetDateTime.now().minusMinutes(30),OffsetDateTime.now())).isEqualTo(1);
        assertThat(jobs.claimPipelineJobForExecution(id,OffsetDateTime.now())).isEqualTo(1);
    }
    @Test void migrationPreservesExistingBindingAndAllowsIndependentScopes() throws Exception {
        String schema="binding_test_"+UUID.randomUUID().toString().replace("-","");
        try (var conn=dataSource.getConnection(); var sql=conn.createStatement()) {
            sql.execute("CREATE SCHEMA "+schema);
            try {
                conn.setSchema(schema);
                sql.execute("CREATE TABLE vcs_repo_binding (workspace_id bigint, vcs_connection_id bigint, provider text, external_repo_id text, CONSTRAINT uq_vcs_repo_binding_external UNIQUE(provider, external_repo_id))");
                sql.execute("INSERT INTO vcs_repo_binding VALUES (1, 10, 'GITLAB', '42')");
                try(var source=getClass().getResourceAsStream("/db/migration/managed/V2.32.0__scope_repository_bindings.sql")) {
                    assertThat(source).isNotNull();
                    sql.execute(new String(source.readAllBytes(),java.nio.charset.StandardCharsets.UTF_8));
                }
                sql.execute("INSERT INTO vcs_repo_binding VALUES (2, 20, 'GITLAB', '42'), (1, 30, 'GITLAB', '42')");
                try(var rows=sql.executeQuery("SELECT count(*) FROM vcs_repo_binding")) { rows.next(); assertThat(rows.getInt(1)).isEqualTo(3); }
                assertThatThrownBy(()->sql.execute("INSERT INTO vcs_repo_binding VALUES (1,10,'GITLAB','42')"))
                        .isInstanceOf(java.sql.SQLException.class).hasMessageContaining("uq_vcs_repo_binding_scoped_external");
            } finally { conn.setSchema("public"); sql.execute("DROP SCHEMA "+schema+" CASCADE"); }
        }
    }
    @Test @Transactional void historicalIssueLookupRequiresCurrentProjectAndRetainsBaseAnchor() {
        var owner = binding("old", "repo", "one", workspace()).getProject();
        var other = binding("other", "repo", "two", workspace()).getProject();
        var analysis = new CodeAnalysis(); analysis.setProject(owner);
        analysis.setAnalysisType(AnalysisType.PR_REVIEW); em.persist(analysis);
        var issue = new CodeAnalysisIssue(); issue.setAnalysis(analysis);
        issue.setSeverity(IssueSeverity.HIGH); issue.setFilePath("removed.java");
        issue.setLineNumber(7); issue.setAnchorSide(IssueAnchorSide.BASE);
        issue.setSuggestedFixDescription("project-private fix"); em.persist(issue);
        em.flush(); Long id = issue.getId(); em.clear();
        assertThat(issues.findByIdAndAnalysis_Project_Id(id, other.getId())).isEmpty();
        var sameProject = issues.findByIdAndAnalysis_Project_Id(id, owner.getId()).orElseThrow();
        assertThat(sameProject.getAnchorSide()).isEqualTo(IssueAnchorSide.BASE);
        assertThat(sameProject.getLineNumber()).isEqualTo(7);
        assertThat(sameProject.getSuggestedFixDescription()).isEqualTo("project-private fix");
    }
    @Test void anchorMigrationPreservesExistingRowsWithHeadDefault() throws Exception {
        String schema = "anchor_test_" + UUID.randomUUID().toString().replace("-", "");
        try (var conn = dataSource.getConnection(); var sql = conn.createStatement()) {
            sql.execute("CREATE SCHEMA " + schema);
            try {
                conn.setSchema(schema);
                sql.execute("CREATE TABLE code_analysis_issue (id bigint PRIMARY KEY)");
                sql.execute("CREATE TABLE branch_issue (id bigint PRIMARY KEY)");
                sql.execute("INSERT INTO code_analysis_issue VALUES (1)");
                sql.execute("INSERT INTO branch_issue VALUES (1)");
                try (var source = getClass().getResourceAsStream("/db/migration/managed/V2.33.0__review_anchor_side.sql")) {
                    assertThat(source).isNotNull();
                    sql.execute(new String(source.readAllBytes(), java.nio.charset.StandardCharsets.UTF_8));
                }
                for (String table : java.util.List.of("code_analysis_issue", "branch_issue")) {
                    try (var rows = sql.executeQuery("SELECT anchor_side FROM " + table + " WHERE id=1")) {
                        rows.next(); assertThat(rows.getString(1)).isEqualTo("HEAD");
                    }
                    sql.execute("INSERT INTO " + table + " (id,anchor_side) VALUES (2,'BASE')");
                    try (var rows = sql.executeQuery("SELECT anchor_side FROM " + table + " WHERE id=2")) {
                        rows.next(); assertThat(rows.getString(1)).isEqualTo("BASE");
                    }
                }
            } finally { conn.setSchema("public"); sql.execute("DROP SCHEMA " + schema + " CASCADE"); }
        }
    }


    @Autowired org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository changeSummaries;

    @Test @Transactional void changeSummarySurvivesReloadAndRequiresProjectPrAndRevision() {
        var owner = binding("summary-owner", "repo", "one", workspace()).getProject();
        var other = binding("summary-other", "repo", "two", workspace()).getProject();
        var analysis = new CodeAnalysis(); analysis.setProject(owner);
        analysis.setAnalysisType(AnalysisType.PR_REVIEW); analysis.setPrNumber(9L);
        analysis.setCommitHash("abc123"); em.persist(analysis); em.flush();
        String payload = "{\"summary\":\"Added input guard\",\"files\":[{\"path\":\"a.py\"}]}";
        analysis.setPrChangeSummaryPayload(payload); changeSummaries.saveAndFlush(analysis);
        em.clear();
        assertThat(changeSummaries.findByProjectIdAndCommitHashAndPrNumber(
                owner.getId(), "abc123", 9L)).get().extracting(CodeAnalysis::getPrChangeSummaryPayload).isEqualTo(payload);
        assertThat(changeSummaries.findByProjectIdAndCommitHashAndPrNumber(
                other.getId(), "abc123", 9L)).isEmpty();
        assertThat(changeSummaries.findByProjectIdAndCommitHashAndPrNumber(
                owner.getId(), "abc123", 10L)).isEmpty();
        assertThat(changeSummaries.findByProjectIdAndCommitHashAndPrNumber(
                owner.getId(), "new123", 9L)).isEmpty();
    }

    @Test void changeSummaryMigrationStoresGraphAndCascadesDeletion() throws Exception {
        String schema = "summary_test_" + UUID.randomUUID().toString().replace("-", "");
        try (var conn = dataSource.getConnection(); var sql = conn.createStatement()) {
            sql.execute("CREATE SCHEMA " + schema);
            try {
                conn.setSchema(schema);
                sql.execute("CREATE TABLE project (id bigint PRIMARY KEY)");
                sql.execute("CREATE TABLE code_analysis (id bigint PRIMARY KEY)");
                try (var source = getClass().getResourceAsStream("/db/migration/managed/V2.35.0__pr_change_summary.sql")) {
                    assertThat(source).isNotNull();
                    sql.execute(new String(source.readAllBytes(), java.nio.charset.StandardCharsets.UTF_8));
                }
                sql.execute("INSERT INTO project VALUES (7); INSERT INTO code_analysis VALUES (101)");
                sql.execute("INSERT INTO pr_change_summary (analysis_id,project_id,pr_number,source_revision,payload) VALUES (101,7,9,'abc','{\"files\":[]}')");
                try (var rows = sql.executeQuery("SELECT payload FROM pr_change_summary WHERE project_id=7 AND pr_number=9 AND source_revision='abc'")) {
                    assertThat(rows.next()).isTrue(); assertThat(rows.getString(1)).isEqualTo("{\"files\":[]}");
                }
                sql.execute("DELETE FROM code_analysis WHERE id=101");
                try (var rows = sql.executeQuery("SELECT count(*) FROM pr_change_summary")) {
                    rows.next(); assertThat(rows.getInt(1)).isZero();
                }
            } finally { conn.setSchema("public"); sql.execute("DROP SCHEMA " + schema + " CASCADE"); }
        }
    }
}
