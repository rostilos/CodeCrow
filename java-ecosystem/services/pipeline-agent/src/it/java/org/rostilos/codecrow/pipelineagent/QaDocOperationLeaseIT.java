package org.rostilos.codecrow.pipelineagent;

import jakarta.persistence.EntityManager;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.analysis.AnalysisLockRepository;
import org.rostilos.codecrow.core.persistence.repository.qadoc.QaDocDocumentRepository;
import org.rostilos.codecrow.core.service.QaDocDocumentService;
import org.rostilos.codecrow.pipelineagent.qadoc.QaDocOperationService;
import org.rostilos.codecrow.testsupport.base.IntegrationTest;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.SpringBootConfiguration;
import org.springframework.boot.autoconfigure.EnableAutoConfiguration;
import org.springframework.boot.autoconfigure.domain.EntityScan;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.annotation.Import;
import org.springframework.data.jpa.repository.config.EnableJpaRepositories;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;
import java.util.UUID;
import java.util.concurrent.*;
import static org.assertj.core.api.Assertions.*;

@IntegrationTest
@SpringBootTest(classes=QaDocOperationLeaseIT.Config.class, properties={"spring.flyway.enabled=false", "spring.jpa.hibernate.ddl-auto=create-drop", "analysis.lock.wait.retry.interval.seconds=1"})
class QaDocOperationLeaseIT {
    @SpringBootConfiguration @EnableAutoConfiguration
    @EntityScan("org.rostilos.codecrow.core.model")
    @EnableJpaRepositories(basePackageClasses={AnalysisLockRepository.class, QaDocDocumentRepository.class})
    @Import({AnalysisLockService.class, QaDocOperationService.class, QaDocDocumentService.class})
    static class Config {}
    @Autowired EntityManager em;
    @Autowired PlatformTransactionManager transactions;
    @Autowired QaDocOperationService operations;
    @Autowired AnalysisLockService locks;
    @Autowired AnalysisLockRepository lockRepository;
    @Autowired QaDocDocumentService documents;

    private Project project() {
        return new TransactionTemplate(transactions).execute(status -> {
            String name = UUID.randomUUID().toString(); var workspace = new Workspace(name, name, "QA operation"); em.persist(workspace);
            var project = new Project(); project.setNamespace(name); project.setWorkspace(workspace); em.persist(project); return project;
        });
    }

    @ParameterizedTest @ValueSource(booleans={false,true})
    void serializesSharedTaskOrSharedPrBeforeReadingHistory(boolean samePr) throws Exception {
        var project = project(); var pool = Executors.newSingleThreadExecutor(); var waiting = new CountDownLatch(1);
        try (var first = operations.open(project, 7L, "TASK-1", ignored -> {})) {
            Future<String> next = pool.submit(() -> {
                try (var second = operations.open(project, samePr ? 7L : 8L, samePr ? "TASK-2" : "task-1",
                        event -> { if ("lock_wait".equals(event.get("type"))) waiting.countDown(); })) {
                    return documents.findLatestDocumentForTask(project.getId(), "TASK-1", null).orElseThrow().getMarkdownContent();
                }
            });
            assertThat(waiting.await(10, TimeUnit.SECONDS)).isTrue(); assertThat(next.isDone()).isFalse();
            first.commit(() -> documents.upsertLatestDocument(project, 7L, "TASK-1", 100L, "head", "committed task coverage"));
            first.close();
            assertThat(next.get(15, TimeUnit.SECONDS)).isEqualTo("committed task coverage");
        } finally { pool.shutdownNow(); }
    }

    @Test void migrationPreservesDocumentsAndAllowsQaLeasesOnHibernateCreatedSchemas() throws Exception {
        String sql;
        try (var input = getClass().getClassLoader().getResourceAsStream("db/migration/managed/V2.39.0__qa_document_concurrency.sql")) {
            sql = new String(input.readAllBytes(), java.nio.charset.StandardCharsets.UTF_8);
        }
        String migration = sql;
        new TransactionTemplate(transactions).executeWithoutResult(status -> {
            String schema = "qa_upgrade_" + UUID.randomUUID().toString().replace("-", "");
            em.createNativeQuery("CREATE SCHEMA " + schema).executeUpdate();
            em.createNativeQuery("SET LOCAL search_path TO " + schema).executeUpdate();
            em.createNativeQuery("CREATE TABLE qa_doc_document(id bigint, markdown_content text)").executeUpdate();
            em.createNativeQuery("INSERT INTO qa_doc_document VALUES (1, 'retained coverage')").executeUpdate();
            em.createNativeQuery("CREATE TABLE analysis_lock (analysis_type varchar(50) CHECK (analysis_type IN ('PR_ANALYSIS','BRANCH_ANALYSIS','RAG_INDEXING')))").executeUpdate();
            em.createNativeQuery(migration).executeUpdate();
            em.createNativeQuery("INSERT INTO analysis_lock VALUES ('QA_DOCUMENTATION')").executeUpdate();
            em.createNativeQuery("DO $$ BEGIN INSERT INTO analysis_lock VALUES ('UNKNOWN'); RAISE EXCEPTION 'invalid lock type accepted'; EXCEPTION WHEN check_violation THEN NULL; END $$").executeUpdate();
            assertThat(em.createNativeQuery("SELECT markdown_content FROM qa_doc_document WHERE id=1").getSingleResult()).isEqualTo("retained coverage");
            assertThat(((Number) em.createNativeQuery("SELECT row_version FROM qa_doc_document WHERE id=1").getSingleResult()).longValue()).isZero();
            em.createNativeQuery("DROP SCHEMA " + schema + " CASCADE").executeUpdate();
        });
    }

    @Test void replacedOwnerCannotPublishAndCleanupKeepsTheReplacementLease() {
        var project = project();
        try (var operation = operations.open(project, 7L, "TASK-1", ignored -> {})) {
            operation.assertOwnership();
            var old = lockRepository.findByProjectIdAndBranchNameAndAnalysisType(project.getId(), "qa-task:TASK-1", AnalysisLockType.QA_DOCUMENTATION).orElseThrow();
            new TransactionTemplate(transactions).executeWithoutResult(status -> lockRepository.deleteById(old.getId()));
            String replacement = locks.acquireLock(project, "qa-task:TASK-1", AnalysisLockType.QA_DOCUMENTATION).orElseThrow();
            assertThatThrownBy(() -> operation.commit(() -> documents.upsertLatestDocument(project, 7L, "TASK-1", 100L, "head", "stale result")))
                    .isInstanceOf(IllegalStateException.class);
            assertThat(documents.findLatestDocument(project.getId(), 7L)).isEmpty();
            operation.close();
            assertThat(lockRepository.findByLockKey(replacement)).isPresent();
            locks.releaseLock(replacement);
        }
    }
}
