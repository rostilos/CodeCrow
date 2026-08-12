package org.rostilos.codecrow.pipelineagent.qadoc;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.analysisengine.service.pr.PrFileEnrichmentService;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.core.persistence.repository.qadoc.QaDocStateRepository;
import org.rostilos.codecrow.core.persistence.repository.taskmanagement.TaskManagementConnectionRepository;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.core.service.QaDocDocumentService;
import org.rostilos.codecrow.events.analysis.AnalysisCompletedEvent;
import org.rostilos.codecrow.taskmanagement.TaskManagementClientFactory;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;

import java.time.Duration;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class QaAutoDocListenerTest {

    @Mock
    private ProjectRepository projectRepository;
    @Mock
    private TaskManagementConnectionRepository connectionRepository;
    @Mock
    private TaskManagementClientFactory clientFactory;
    @Mock
    private QaDocGenerationService qaDocGenerationService;
    @Mock
    private CodeAnalysisService codeAnalysisService;
    @Mock
    private VcsClientProvider vcsClientProvider;
    @Mock
    private QaDocStateRepository qaDocStateRepository;
    @Mock
    private QaDocDocumentService qaDocDocumentService;
    @Mock
    private QaDocPublicPreviewService qaDocPublicPreviewService;
    @Mock
    private PrFileEnrichmentService enrichmentService;
    @Test
    void loadsProjectWithVcsConnectionsForAsyncProcessing() {
        QaAutoDocListener listener = new QaAutoDocListener(
                projectRepository,
                connectionRepository,
                clientFactory,
                qaDocGenerationService,
                codeAnalysisService,
                vcsClientProvider,
                qaDocStateRepository,
                qaDocDocumentService,
                qaDocPublicPreviewService,
                enrichmentService);
        AnalysisCompletedEvent event = new AnalysisCompletedEvent(
                this,
                "correlation-id",
                402L,
                null,
                AnalysisCompletedEvent.CompletionStatus.SUCCESS,
                Duration.ZERO,
                0,
                0,
                null,
                Map.of(),
                "workspace",
                "repository",
                17L);
        when(projectRepository.findByIdWithFullDetails(402L)).thenReturn(Optional.empty());

        listener.onAnalysisCompleted(event);

        verify(projectRepository).findByIdWithFullDetails(402L);
        verify(projectRepository, never()).findById(402L);
    }

    @Test
    void distinguishesLegacyLinkOnlyCommentsFromRedactedFullDocuments() {
        String previewUrl = "https://codecrow.cloud/share#token=ccs_opaque-token";

        assertThat(QaAutoDocListener.isPublicPreviewOnlyComment(previewUrl)).isTrue();
        assertThat(QaAutoDocListener.isPublicPreviewOnlyComment(
                "[View QA test cases in CodeCrow](" + previewUrl + ")"))
                .isTrue();
        assertThat(QaAutoDocListener.isPublicPreviewOnlyComment("""
                ### 1. Change Summary
                Login validation changed.

                ### 3. Test Scenarios
                %s

                ### 4. Edge Cases
                Verify an empty password.
                """.formatted(previewUrl)))
                .isFalse();
    }
}
