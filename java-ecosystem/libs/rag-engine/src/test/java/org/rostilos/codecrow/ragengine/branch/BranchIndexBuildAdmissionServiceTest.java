package org.rostilos.codecrow.ragengine.branch;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InOrder;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.job.JobTriggerSource;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.rag.RagBranchIndex;
import org.rostilos.codecrow.core.model.rag.RagBranchIndexGeneration;
import org.rostilos.codecrow.core.model.rag.RagBranchIndexKind;
import org.rostilos.codecrow.core.model.rag.RagIndexOperation;
import org.rostilos.codecrow.core.service.AnalysisJobService;
import org.rostilos.codecrow.ragengine.service.RagBranchIndexRegistryService;
import org.rostilos.codecrow.ragengine.service.RagIndexTrackingService;
import org.springframework.test.util.ReflectionTestUtils;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class BranchIndexBuildAdmissionServiceTest {

    @Mock private RagBranchIndexRegistryService registryService;
    @Mock private AnalysisJobService jobService;
    @Mock private RagIndexTrackingService trackingService;

    private BranchIndexBuildAdmissionService service;
    private Project project;
    private RagBranchIndexRegistryService.BuildRegistration registration;

    @BeforeEach
    void setUp() {
        service = new BranchIndexBuildAdmissionService(
                registryService, jobService, trackingService);
        project = new Project();
        ReflectionTestUtils.setField(project, "id", 42L);
        RagBranchIndex branchIndex = new RagBranchIndex(
                project, "main", RagBranchIndexKind.PRIMARY);
        branchIndex.setId(10L);
        RagBranchIndexGeneration source = new RagBranchIndexGeneration(
                branchIndex, "revision-a", "source-target", null, null, null);
        source.setId(19L);
        source.activate("source-manifest", 120, 240);
        RagBranchIndexGeneration generation = new RagBranchIndexGeneration(
                branchIndex, "revision-b", "physical-target", source, null, null);
        generation.setId(20L);
        RagIndexOperation operation = new RagIndexOperation(
                project, "main", null, "revision-b", "operation-key");
        operation.setId(30L);
        operation.setGeneration(generation);
        registration = new RagBranchIndexRegistryService.BuildRegistration(
                branchIndex, generation, operation, false);
    }

    @Test
    void registersThenAtomicallyLinksAndStartsJobAndOperation() {
        when(registryService.registerBuild(
                eq(project), eq("main"), eq(RagBranchIndexKind.PRIMARY),
                isNull(), eq("revision-b"), startsWith("exact-full-snapshot:automatic:")))
                .thenReturn(registration);
        Job job = mock(Job.class);
        when(job.getId()).thenReturn(77L);
        when(jobService.createRagIndexJob(
                project, false, JobTriggerSource.WEBHOOK, "main", "revision-b"))
                .thenReturn(job);

        var admitted = service.admit(
                project, "main", "revision-b", RagBranchIndexKind.PRIMARY,
                JobTriggerSource.WEBHOOK, "lock-owner-123",
                BranchIndexBuildAdmissionService.BuildOrigin.AUTOMATIC);

        assertThat(admitted.job()).isSameAs(job);
        assertThat(admitted.preparedBuild().operationId()).isEqualTo(30L);
        assertThat(admitted.preparedBuild().collectionTarget())
                .isEqualTo("physical-target");
        assertThat(admitted.preparedBuild().analysisLockKey())
                .isEqualTo("lock-owner-123");
        assertThat(admitted.statusAdmission()).isEqualTo(
                BranchIndexBuildAdmissionService.ProjectStatusAdmission.UPDATING);
        InOrder order = inOrder(registryService, jobService, trackingService);
        order.verify(registryService).registerBuild(
                eq(project), eq("main"), eq(RagBranchIndexKind.PRIMARY),
                isNull(), eq("revision-b"), startsWith("exact-full-snapshot:automatic:"));
        order.verify(trackingService).preparePublishedGenerationForUpdate(
                project, "main", "revision-a", 120, 240);
        order.verify(jobService).createRagIndexJob(
                project, false, JobTriggerSource.WEBHOOK, "main", "revision-b");
        order.verify(registryService).startBuild(30L, 77L, "lock-owner-123");
        order.verify(jobService).startJob(job);
        order.verify(trackingService).markUpdatingStarted(
                project, "main", "revision-b", 77L);
    }

    @Test
    void rejectsPreviouslyCommittedAdmissionWithoutCreatingAnotherJob() {
        when(registryService.registerBuild(any(), anyString(), any(), isNull(),
                anyString(), anyString()))
                .thenReturn(new RagBranchIndexRegistryService.BuildRegistration(
                        registration.branchIndex(), registration.generation(),
                        registration.operation(), true));

        assertThatThrownBy(() -> service.admit(
                project, "main", "revision-b", RagBranchIndexKind.PRIMARY,
                JobTriggerSource.WEBHOOK, "same-lock",
                BranchIndexBuildAdmissionService.BuildOrigin.AUTOMATIC))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("already admitted");

        verifyNoInteractions(jobService);
        verify(registryService, never()).startBuild(anyLong(), any(), anyString());
    }

    @Test
    void initialPrimaryAdmissionStartsIndexingWithoutInventingASourceCheckpoint() {
        RagBranchIndexGeneration initial = new RagBranchIndexGeneration(
                registration.branchIndex(), "revision-first", "initial-target",
                null, null, null);
        initial.setId(21L);
        RagIndexOperation initialOperation = new RagIndexOperation(
                project, "main", null, "revision-first", "initial-operation");
        initialOperation.setId(31L);
        initialOperation.setGeneration(initial);
        when(registryService.registerBuild(any(), anyString(), any(), isNull(),
                eq("revision-first"), anyString()))
                .thenReturn(new RagBranchIndexRegistryService.BuildRegistration(
                        registration.branchIndex(), initial, initialOperation, false));
        Job job = mock(Job.class);
        when(job.getId()).thenReturn(78L);
        when(jobService.createRagIndexJob(
                project, true, JobTriggerSource.WEBHOOK, "main", "revision-first"))
                .thenReturn(job);

        var admitted = service.admit(
                project, "main", "revision-first", RagBranchIndexKind.PRIMARY,
                JobTriggerSource.WEBHOOK, "initial-lock",
                BranchIndexBuildAdmissionService.BuildOrigin.AUTOMATIC);

        assertThat(admitted.statusAdmission()).isEqualTo(
                BranchIndexBuildAdmissionService.ProjectStatusAdmission.INDEXING);
        verify(trackingService).markIndexingStarted(
                project, "main", "revision-first", 78L);
        verify(trackingService, never()).preparePublishedGenerationForUpdate(
                any(), anyString(), anyString(), any(), any());
        verify(trackingService, never()).markUpdatingStarted(
                any(), anyString(), anyString(), any());
    }
}
