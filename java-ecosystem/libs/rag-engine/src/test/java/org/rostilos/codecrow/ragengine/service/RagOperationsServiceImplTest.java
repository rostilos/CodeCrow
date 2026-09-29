package org.rostilos.codecrow.ragengine.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.project.config.RagConfig;
import org.rostilos.codecrow.core.model.rag.RagBranchIndexGenerationStatus;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.rag.RagBranchIndexGenerationRepository;
import org.rostilos.codecrow.core.persistence.repository.rag.RagBranchIndexRepository;
import org.rostilos.codecrow.core.service.AnalysisJobService;
import org.rostilos.codecrow.core.service.RepositoryIndexJobQueueService;
import org.rostilos.codecrow.ragengine.branch.BranchIndexBuildAdmissionService;
import org.rostilos.codecrow.ragengine.branch.BranchIndexGenerationBuildService;
import org.rostilos.codecrow.ragengine.client.RagPipelineClient;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class RagOperationsServiceImplTest {

    @Mock private RagIndexTrackingService trackingService;
    @Mock private AnalysisLockService lockService;
    @Mock private AnalysisJobService jobService;
    @Mock private RagBranchIndexRepository branchIndexRepository;
    @Mock private RagBranchIndexGenerationRepository generationRepository;
    @Mock private RagPipelineClient pipelineClient;
    @Mock private BranchIndexGenerationBuildService generationBuildService;
    @Mock private BranchIndexBuildAdmissionService buildAdmissionService;
    @Mock private RepositoryIndexJobQueueService queueService;
    @Mock private RagRepresentationIdentityService representationIdentityService;

    private RagOperationsServiceImpl service;
    private Project project;

    @BeforeEach
    void setUp() {
        service = new RagOperationsServiceImpl(
                trackingService,
                lockService,
                jobService,
                branchIndexRepository,
                generationRepository,
                pipelineClient,
                generationBuildService,
                buildAdmissionService,
                queueService,
                representationIdentityService);
        ReflectionTestUtils.setField(service, "ragApiEnabled", true);

        project = new Project();
        ReflectionTestUtils.setField(project, "id", 42L);
        project.setNamespace("repository");
        Workspace workspace = new Workspace();
        ReflectionTestUtils.setField(workspace, "name", "workspace");
        project.setWorkspace(workspace);
        ProjectConfig config = new ProjectConfig();
        config.setMainBranch("main");
        config.setRagConfig(new RagConfig(true, "main"));
        project.setConfiguration(config);
    }

    @Test
    void readinessComesOnlyFromAnActiveExactGeneration() {
        var active = mock(RagBranchIndexRepository.ActiveGenerationCoordinates.class);
        when(branchIndexRepository.existsByProjectIdAndBranchName(42L, "main"))
                .thenReturn(true);
        when(branchIndexRepository.markAccessedIfUnclaimed(eq(42L), eq("main"), any()))
                .thenReturn(1);
        when(branchIndexRepository.findActiveGenerationCoordinates(42L, "main"))
                .thenReturn(Optional.of(active));

        assertThat(service.isRagIndexReady(project)).isTrue();
        verifyNoInteractions(trackingService);
    }

}
