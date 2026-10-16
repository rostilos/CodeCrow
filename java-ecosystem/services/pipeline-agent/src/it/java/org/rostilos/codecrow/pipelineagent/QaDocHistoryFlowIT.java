package org.rostilos.codecrow.pipelineagent;

import jakarta.persistence.EntityManager;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.codeanalysis.*;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.*;
import org.junit.jupiter.params.provider.ValueSource;
import org.mockito.ArgumentCaptor;
import org.rostilos.codecrow.analysisengine.service.pr.PrFileEnrichmentService;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.*;
import org.rostilos.codecrow.core.model.qadoc.QaDocState;
import org.rostilos.codecrow.core.model.taskmanagement.*;
import org.rostilos.codecrow.core.model.vcs.*;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.core.persistence.repository.qadoc.*;
import org.rostilos.codecrow.core.persistence.repository.taskmanagement.TaskManagementConnectionRepository;
import org.rostilos.codecrow.core.service.*;
import org.rostilos.codecrow.events.analysis.AnalysisCompletedEvent;
import org.rostilos.codecrow.pipelineagent.generic.dto.webhook.WebhookPayload;
import org.rostilos.codecrow.pipelineagent.generic.processor.command.QaDocCommandProcessor;
import org.rostilos.codecrow.pipelineagent.qadoc.*;
import org.rostilos.codecrow.taskmanagement.*;
import org.rostilos.codecrow.vcsclient.*;
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
import java.time.Duration;
import java.util.*;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

/** Committed task/PR history through both QA entry points; only external providers/model are mocked. */
@IntegrationTest
@SpringBootTest(classes=QaDocHistoryFlowIT.Config.class,
        properties={"spring.flyway.enabled=false", "spring.jpa.hibernate.ddl-auto=create-drop"})
class QaDocHistoryFlowIT {
    @SpringBootConfiguration @EnableAutoConfiguration
    @EntityScan("org.rostilos.codecrow.core.model")
    @EnableJpaRepositories(basePackageClasses={QaDocDocumentRepository.class, CodeAnalysisRepository.class})
    @Import(QaDocDocumentService.class)
    static class Config {}
    @Autowired EntityManager em;
    @Autowired PlatformTransactionManager transactions;
    @Autowired QaDocDocumentService documents;
    @Autowired QaDocDocumentRepository documentRepository;
    @Autowired QaDocStateRepository states;
    @Autowired CodeAnalysisRepository analysisRepository;

    @ParameterizedTest @ValueSource(booleans={false,true})
    void rerunUsesOwnPrCommitAndLatestTaskCoverageAfterReload(boolean automatic) throws Exception { exercise(automatic, false); }

    @Test void delayedEventKeepsItsExactReviewAfterANewerReviewAtTheSameHead() throws Exception { exercise(true, true); }

    private Project newProject() {
        return new TransactionTemplate(transactions).execute(status -> {
            String name = UUID.randomUUID().toString(); var workspace = new Workspace(name, name, "QA ordering"); em.persist(workspace);
            var project = new Project(); project.setNamespace(name); project.setWorkspace(workspace); em.persist(project); return project;
        });
    }

    @Test void olderReviewCannotOverwriteCommittedNewerDocumentation() {
        var project = newProject();
        documents.upsertLatestDocument(project, 7L, "TASK-123", 101L, "new-head", "new coverage");
        assertThatThrownBy(() -> documents.upsertLatestDocument(project, 7L, "TASK-123", 100L, "old-head", "old coverage"))
                .isInstanceOf(IllegalStateException.class);
        assertThat(documents.findLatestDocument(project.getId(), 7L).orElseThrow().getMarkdownContent()).isEqualTo("new coverage");
    }

    @Test void staleDetachedWriterCannotReplaceACommittedDocument() {
        var project = newProject();
        var initial = documents.upsertLatestDocument(project, 7L, "TASK-123", 100L, "head", "original");
        var stale = documentRepository.findById(initial.getId()).orElseThrow();
        documents.upsertLatestDocument(project, 7L, "TASK-123", 100L, "head", "updated coverage");
        stale.replaceContent("TASK-123", 100L, "head", "stale coverage");
        assertThatThrownBy(() -> documentRepository.saveAndFlush(stale))
                .isInstanceOf(org.springframework.dao.OptimisticLockingFailureException.class);
        assertThat(documents.findLatestDocument(project.getId(), 7L).orElseThrow().getMarkdownContent()).isEqualTo("updated coverage");
    }

    private void exercise(boolean automatic, boolean exactReview) throws Exception {
        var tx = new TransactionTemplate(transactions);
        Project stored = tx.execute(status -> {
            String name = UUID.randomUUID().toString();
            var workspace = new Workspace(name, name, "QA history"); em.persist(workspace);
            var project = new Project(); project.setNamespace(name); project.setWorkspace(workspace); em.persist(project);
            return project;
        });
        Project project = spy(stored);
        var config = new ProjectConfig();
        config.setQaAutoDoc(new QaAutoDocConfig(true, QaAutoDocConfig.TemplateMode.BASE, null, null, null));
        config.setTaskManagement(new TaskManagementConfig(1L, TaskManagementConfig.DEFAULT_TASK_ID_PATTERN, TaskManagementConfig.TaskIdSource.BRANCH_NAME));
        doReturn(config).when(project).getConfiguration();
        var repo = mock(VcsRepoInfo.class); var vcsConnection = mock(VcsConnection.class);
        doReturn(repo).when(project).getEffectiveVcsRepoInfo();
        when(repo.getVcsConnection()).thenReturn(vcsConnection); when(repo.getRepoWorkspace()).thenReturn("workspace");
        when(repo.getRepoSlug()).thenReturn("repo"); when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.GITHUB);
        var vcs = mock(VcsClient.class); var providers = mock(VcsClientProvider.class);
        when(providers.getClient(vcsConnection)).thenReturn(vcs);
        when(vcs.getPullRequest("workspace", "repo", 7L)).thenReturn(new org.rostilos.codecrow.vcsclient.model.VcsPullRequest(7L, "title", "body", "feature", "main", "base", "a-new", "open", false, "url"));
        when(vcs.getCommitRangeDiff(eq("workspace"), eq("repo"), anyString(), eq("a-new"))).thenReturn("delta");
        documents.upsertLatestDocument(stored, 7L, "TASK-123", null, "a-old", "PR A only");
        documents.upsertLatestDocument(stored, 8L, "TASK-123", null, "b-new", "PR A and PR B coverage");
        var state = new QaDocState(stored, "TASK-123");
        state.recordGeneration("a-old", null, 7L); state.recordGeneration("b-new", null, 8L); states.saveAndFlush(state);
        var connections = mock(TaskManagementConnectionRepository.class);
        var connection = new TaskManagementConnection(); connection.setProviderType(ETaskManagementProvider.JIRA_CLOUD);
        connection.setBaseUrl("https://jira.example"); connection.setCredentials(Map.of("email", "test@example.com", "apiToken", "test"));
        when(connections.findByIdAndWorkspaceId(1L, stored.getWorkspace().getId())).thenReturn(Optional.of(connection));
        var clients = mock(TaskManagementClientFactory.class); var client = mock(TaskManagementClient.class);
        when(clients.createClient(any(), anyString(), anyString(), anyString())).thenReturn(client);
        var generation = mock(QaDocGenerationService.class); var analyses = mock(CodeAnalysisService.class);
        var summaries = exactReview ? new PrChangeSummaryService(analysisRepository, new com.fasterxml.jackson.databind.ObjectMapper())
                : mock(PrChangeSummaryService.class);
        Long expectedAnalysisId = null;
        if (exactReview) {
            expectedAnalysisId = tx.execute(status -> {
                Long firstId = null;
                for (int version = 1; version <= 2; version++) {
                    var analysis = new CodeAnalysis(); analysis.setProject(stored); analysis.setAnalysisType(AnalysisType.PR_REVIEW);
                    analysis.setCommitHash("a-new"); analysis.setPrNumber(7L); analysis.setPrVersion(version);
                    summaries.attach(analysis, Map.of("projectId", stored.getId(), "pullRequestId", 7L,
                            "sourceRevision", "a-new", "toolContext", Map.of("reviewSource", Map.of(
                                    "rawDiff", "saved exact diff " + version, "baseRevision", "base-" + version,
                                    "targetRevision", "target-" + version))));
                    em.persist(analysis); em.flush(); if (firstId == null) firstId = analysis.getId();
                }
                return firstId;
            });
            analyses = new CodeAnalysisService(analysisRepository, mock(CodeAnalysisIssueRepository.class),
                    mock(org.rostilos.codecrow.core.persistence.repository.qualitygate.QualityGateRepository.class),
                    mock(org.rostilos.codecrow.core.service.qualitygate.QualityGateEvaluator.class), new IssueDeduplicationService(), summaries);
        } else when(summaries.find(eq(project.getId()), eq(7L), eq("a-new"), isNull())).thenReturn(null);
        var operationService = mock(QaDocOperationService.class);
        var operation = mock(QaDocOperationService.Operation.class);
        when(operationService.open(any(), any(), anyString(), any())).thenReturn(operation);
        when(operation.commit(any())).thenAnswer(inv -> ((java.util.function.Supplier<?>) inv.getArgument(0)).get());
        var previews = mock(QaDocPublicPreviewService.class);
        var enrichment = mock(PrFileEnrichmentService.class);
        var context = ArgumentCaptor.forClass(QaDocGenerationContext.class);
        // Return no new content: this test observes preparation without an external publication.
        var command = new QaDocCommandProcessor(connections, clients, generation, analyses, providers,
                states, documents, previews, enrichment, summaries, operationService);
        if (automatic) {
            var sourceReviews=mock(CodeAnalysisRepository.class);
            var input=expectedAnalysisId == null ? new CodeAnalysis() : analysisRepository.findById(expectedAnalysisId).orElseThrow();
            input.setPrNumber(7L); input.setCommitHash("a-new"); input.setSourceBranchName("feature/TASK-123"); input.setBranchName("main");
            long sourceId=expectedAnalysisId == null ? 0L : expectedAnalysisId;
            when(sourceReviews.findByIdAndProjectId(sourceId,project.getId())).thenReturn(Optional.of(input));
            var jobs=mock(JobService.class);
            var job=new org.rostilos.codecrow.core.model.job.Job(); job.setProject(project); job.setPrNumber(7L);
            job.setCommitHash("a-new"); job.setInputAnalysisId(sourceId);
            when(jobs.claimQaJobForExecution(job)).thenReturn(true);
            var webhooks=mock(org.rostilos.codecrow.pipelineagent.generic.processor.WebhookAsyncProcessor.class);
            when(webhooks.loadAndInitializeProject(project.getId())).thenReturn(project);
            var scheduler=new QaDocRecoveryScheduler(sourceReviews,mock(org.rostilos.codecrow.core.persistence.repository.job.JobRepository.class),
                    mock(QaDocQueueService.class),jobs,webhooks,
                    mock(org.rostilos.codecrow.pipelineagent.generic.webhookhandler.WebhookHandlerFactory.class),command,summaries,
                    new com.fasterxml.jackson.databind.ObjectMapper(),Runnable::run);
            org.springframework.test.util.ReflectionTestUtils.invokeMethod(scheduler,"executeAutomatic",job);
            verify(jobs, never()).failJob(any(),anyString());
        } else {
            command.process(new WebhookPayload(EVcsProvider.GITHUB, "issue_comment", "repo-id", "repo", "workspace",
                    "7", "feature/TASK-123", "main", "a-new", null), project, ignored -> {}, Map.of());
        }
        verify(generation).generateQaDocumentation(eq(project), eq(7L), anyInt(), anyInt(), anyMap(), context.capture());
        if (exactReview) {
            assertThat(context.getValue().analysis().getId()).isEqualTo(expectedAnalysisId);
            assertThat(context.getValue().diff()).isEqualTo("saved exact diff 1");
            verify(vcs, never()).getPullRequestDiff(anyString(), anyString(), anyLong());
        }
        verify(vcs).getCommitRangeDiff("workspace", "repo", "a-old", "a-new");
        assertThat(context.getValue().previousDocumentation()).isEqualTo("PR A and PR B coverage");
        assertThat(documents.findLatestDocument(project.getId(), 7L).orElseThrow().getCommitHash()).isEqualTo("a-old");
        assertThat(states.findByProjectIdAndTaskId(project.getId(), "TASK-123").orElseThrow().getLastCommitHash()).isEqualTo("b-new");
    }
}
