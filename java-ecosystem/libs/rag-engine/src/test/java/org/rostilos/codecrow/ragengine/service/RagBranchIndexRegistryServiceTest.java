package org.rostilos.codecrow.ragengine.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.rag.*;
import org.rostilos.codecrow.core.persistence.repository.rag.RagBranchIndexGenerationRepository;
import org.rostilos.codecrow.core.persistence.repository.rag.RagBranchIndexRepository;
import org.rostilos.codecrow.core.persistence.repository.rag.RagIndexOperationRepository;
import org.springframework.test.util.ReflectionTestUtils;

import java.time.OffsetDateTime;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class RagBranchIndexRegistryServiceTest {

    @Mock
    private RagBranchIndexRepository branchIndexRepository;
    @Mock
    private RagBranchIndexGenerationRepository generationRepository;
    @Mock
    private RagIndexOperationRepository operationRepository;

    private RagBranchIndexRegistryService service;
    private Project project;

    @BeforeEach
    void setUp() {
        service = new RagBranchIndexRegistryService(
                branchIndexRepository, generationRepository, operationRepository);
        project = new Project();
        ReflectionTestUtils.setField(project, "id", 42L);
        lenient().when(branchIndexRepository.save(any())).thenAnswer(invocation -> {
            RagBranchIndex value = invocation.getArgument(0);
            if (value.getId() == null) {
                value.setId(10L);
            }
            return value;
        });
        lenient().when(generationRepository.save(any())).thenAnswer(invocation -> {
            RagBranchIndexGeneration value = invocation.getArgument(0);
            if (value.getId() == null) {
                value.setId(20L);
            }
            return value;
        });
        lenient().when(operationRepository.save(any())).thenAnswer(invocation -> {
            RagIndexOperation value = invocation.getArgument(0);
            if (value.getId() == null) {
                value.setId(30L);
            }
            return value;
        });
    }

    @Test
    void registersIdempotentTenantScopedBuildWithoutLeakingBranchName() {
        when(operationRepository.findByProjectIdAndOperationKey(eq(42L), anyString()))
                .thenReturn(Optional.empty());
        when(branchIndexRepository.findByProjectIdAndBranchNameForUpdate(42L, "client/private-develop"))
                .thenReturn(Optional.empty());

        var registration = service.registerBuild(
                project,
                "client/private-develop",
                RagBranchIndexKind.DURABLE,
                "master-100",
                "develop-400",
                "representation");

        assertThat(registration.existingOperation()).isFalse();
        assertThat(registration.branchIndex().getIndexKind()).isEqualTo(RagBranchIndexKind.DURABLE);
        assertThat(registration.branchIndex().getDesiredCommitHash()).isEqualTo("develop-400");
        assertThat(registration.generation().getSeedRevision()).isEqualTo("master-100");
        assertThat(registration.generation().getCollectionName())
                .startsWith("cc_w0_p42_b")
                .doesNotContain("client", "private", "develop");
        assertThat(registration.operation().getOperationKey()).hasSize(64);
    }

    @Test
    void publishesNewGenerationAndSupersedesPreviousOneAtomically() {
        RagBranchIndex branchIndex = new RagBranchIndex(project, "develop", RagBranchIndexKind.DURABLE);
        branchIndex.setId(10L);
        RagBranchIndexGeneration previous = new RagBranchIndexGeneration(
                branchIndex, "develop-400", "generation-400", null,
                "master-100", "representation");
        previous.setId(19L);
        previous.activate("manifest-400", 500, 1500);
        branchIndex.activate(previous);
        when(branchIndexRepository.findByProjectIdAndBranchNameForUpdate(42L, "develop"))
                .thenReturn(Optional.of(branchIndex));
        when(operationRepository.findByProjectIdAndOperationKey(eq(42L), anyString()))
                .thenReturn(Optional.empty());

        var registration = service.registerBuild(
                project, "develop", RagBranchIndexKind.DURABLE,
                "develop-400", "develop-401", "representation");
        when(operationRepository.findByIdForUpdate(30L)).thenReturn(Optional.of(registration.operation()));
        when(branchIndexRepository.findByIdForPublication(10L))
                .thenReturn(Optional.of(branchIndex));

        service.startBuild(30L, 99L, "rag-lock-owner-99");
        RagBranchIndexGeneration published = service.publish(30L, "manifest-401", 501, 1504);

        assertThat(previous.getStatus()).isEqualTo(RagBranchIndexGenerationStatus.SUPERSEDED);
        assertThat(published.getStatus()).isEqualTo(RagBranchIndexGenerationStatus.ACTIVE);
        assertThat(branchIndex.getActiveGeneration()).isSameAs(published);
        assertThat(branchIndex.getCommitHash()).isEqualTo("develop-401");
        assertThat(registration.operation().getStatus()).isEqualTo(RagIndexOperationStatus.SUCCEEDED);
        assertThat(registration.operation().getAttemptCount()).isEqualTo(1);
        assertThat(registration.operation().getJobId()).isEqualTo(99L);
        assertThat(registration.operation().getAnalysisLockKey())
                .isEqualTo("rag-lock-owner-99");
    }

    @Test
    void completedOlderGenerationDoesNotRegressNewerDesiredRevision() {
        RagBranchIndex branchIndex = new RagBranchIndex(
                project, "develop", RagBranchIndexKind.DURABLE);
        branchIndex.setId(10L);
        RagBranchIndexGeneration active = new RagBranchIndexGeneration(
                branchIndex, "develop-400", "generation-400", null,
                "develop-399", "representation");
        active.setId(19L);
        active.activate("manifest-400", 500, 1500);
        branchIndex.activate(active);
        branchIndex.requestRevision("develop-402");

        RagBranchIndexGeneration late = new RagBranchIndexGeneration(
                branchIndex, "develop-401", "generation-401", active,
                "develop-400", "representation");
        late.setId(20L);
        RagIndexOperation operation = new RagIndexOperation(
                project, "develop", "develop-400", "develop-401", "late-key");
        operation.setId(30L);
        operation.setGeneration(late);
        operation.start();
        when(operationRepository.findByIdForUpdate(30L)).thenReturn(Optional.of(operation));
        when(branchIndexRepository.findByIdForPublication(10L))
                .thenReturn(Optional.of(branchIndex));

        RagBranchIndexGeneration published = service.publish(
                30L, "manifest-401", 501, 1504);

        assertThat(published.getStatus())
                .isEqualTo(RagBranchIndexGenerationStatus.SUPERSEDED);
        assertThat(published.getManifestDigest()).isEqualTo("manifest-401");
        assertThat(branchIndex.getActiveGeneration()).isSameAs(active);
        assertThat(branchIndex.getCommitHash()).isEqualTo("develop-400");
        assertThat(branchIndex.getDesiredCommitHash()).isEqualTo("develop-402");
        assertThat(operation.getStatus()).isEqualTo(RagIndexOperationStatus.SUCCEEDED);
        verify(branchIndexRepository, never()).save(branchIndex);
    }

    @Test
    void failedReplacementKeepsLastVerifiedGenerationAvailable() {
        RagBranchIndex branchIndex = new RagBranchIndex(project, "master", RagBranchIndexKind.PRIMARY);
        branchIndex.setId(10L);
        RagBranchIndexGeneration active = new RagBranchIndexGeneration(
                branchIndex, "master-100", "generation-100", null,
                null, "representation");
        active.setId(19L);
        active.activate("manifest-100", 500, 1500);
        branchIndex.activate(active);
        when(branchIndexRepository.findByProjectIdAndBranchNameForUpdate(42L, "master"))
                .thenReturn(Optional.of(branchIndex));
        when(operationRepository.findByProjectIdAndOperationKey(eq(42L), anyString()))
                .thenReturn(Optional.empty());

        var registration = service.registerBuild(
                project, "master", RagBranchIndexKind.PRIMARY,
                "master-100", "master-101", "representation");
        when(operationRepository.findByIdForUpdate(30L)).thenReturn(Optional.of(registration.operation()));
        when(branchIndexRepository.findByIdForPublication(10L))
                .thenReturn(Optional.of(branchIndex));

        service.fail(30L, "vector publication failed");

        assertThat(branchIndex.getActiveGeneration()).isSameAs(active);
        assertThat(branchIndex.getCommitHash()).isEqualTo("master-100");
        assertThat(branchIndex.getDesiredCommitHash()).isEqualTo("master-101");
        assertThat(branchIndex.getLifecycleStatus()).isEqualTo(RagBranchIndexLifecycleStatus.READY);
        assertThat(registration.generation().getStatus()).isEqualTo(RagBranchIndexGenerationStatus.FAILED);
        assertThat(registration.operation().getStatus()).isEqualTo(RagIndexOperationStatus.FAILED);
    }

    @Test
    void failedIdempotentOperationCanRetryWithoutCreatingDuplicateGeneration() {
        RagBranchIndex branchIndex = new RagBranchIndex(
                project, "develop", RagBranchIndexKind.DURABLE);
        branchIndex.setId(10L);
        branchIndex.requestRevision("develop-401");
        RagBranchIndexGeneration generation = new RagBranchIndexGeneration(
                branchIndex, "develop-401", "generation-401", null,
                "develop-400", "representation");
        generation.setId(20L);
        generation.fail("worker restarted");
        RagIndexOperation operation = new RagIndexOperation(
                project, "develop", "develop-400", "develop-401", "key");
        operation.setId(30L);
        operation.setGeneration(generation);
        operation.fail("worker restarted");
        when(operationRepository.findByIdForUpdate(30L)).thenReturn(Optional.of(operation));

        service.startBuild(30L, 91L);

        assertThat(operation.getStatus()).isEqualTo(RagIndexOperationStatus.RUNNING);
        assertThat(operation.getAttemptCount()).isEqualTo(1);
        assertThat(operation.getJobId()).isEqualTo(91L);
        assertThat(generation.getStatus())
                .isEqualTo(RagBranchIndexGenerationStatus.BUILDING);
        assertThat(branchIndex.getDesiredCommitHash()).isEqualTo("develop-401");
        verify(generationRepository).save(generation);
        verify(branchIndexRepository, never()).save(branchIndex);
        verify(operationRepository).save(operation);
    }

    @Test
    void lateFailureDoesNotOverwriteNewerDesiredRevisionState() {
        RagBranchIndex branchIndex = new RagBranchIndex(
                project, "develop", RagBranchIndexKind.DURABLE);
        branchIndex.setId(10L);
        branchIndex.requestRevision("develop-402");
        RagBranchIndexGeneration late = new RagBranchIndexGeneration(
                branchIndex, "develop-401", "generation-401", null,
                "develop-400", "representation");
        late.setId(20L);
        RagIndexOperation operation = new RagIndexOperation(
                project, "develop", "develop-400", "develop-401", "late-key");
        operation.setId(30L);
        operation.setGeneration(late);
        operation.start();
        when(operationRepository.findByIdForUpdate(30L)).thenReturn(Optional.of(operation));
        when(branchIndexRepository.findByIdForPublication(10L))
                .thenReturn(Optional.of(branchIndex));

        service.fail(30L, "late worker failed");

        assertThat(late.getStatus()).isEqualTo(RagBranchIndexGenerationStatus.FAILED);
        assertThat(operation.getStatus()).isEqualTo(RagIndexOperationStatus.FAILED);
        assertThat(branchIndex.getDesiredCommitHash()).isEqualTo("develop-402");
        assertThat(branchIndex.getLifecycleStatus())
                .isEqualTo(RagBranchIndexLifecycleStatus.BUILDING);
        assertThat(branchIndex.getErrorMessage()).isNull();
        verify(branchIndexRepository, never()).save(branchIndex);
    }

    @Test
    void abandonmentClaimRechecksAHeartbeatUnderTheOperationLock() {
        RagIndexOperation operation = new RagIndexOperation(
                project, "develop", "develop-400", "develop-401", "heartbeat-key");
        operation.setId(30L);
        operation.start();
        operation.setUpdatedAt(OffsetDateTime.now());
        when(operationRepository.findByIdForUpdate(30L)).thenReturn(Optional.of(operation));

        boolean failed = service.failIfAbandoned(
                30L, OffsetDateTime.now().minusMinutes(30), "producer abandoned");

        assertThat(failed).isFalse();
        assertThat(operation.getStatus()).isEqualTo(RagIndexOperationStatus.RUNNING);
        verifyNoInteractions(branchIndexRepository);
        verify(operationRepository, never()).save(operation);
    }

    @Test
    void cleanupClaimMakesExactGenerationUnavailableWithoutRefreshingIt() {
        RagBranchIndex branchIndex = new RagBranchIndex(
                project, "feature/expired", RagBranchIndexKind.TRANSIENT);
        branchIndex.setId(10L);
        branchIndex.setCleanupClaimToken("cleanup-owner");
        when(branchIndexRepository.findByProjectIdAndBranchNameForUpdate(
                42L, "feature/expired"))
                .thenReturn(Optional.of(branchIndex));

        assertThat(service.findAvailableGeneration(
                42L, "feature/expired", "revision-100"))
                .isEmpty();

        verify(branchIndexRepository, never()).save(branchIndex);
        verifyNoInteractions(generationRepository);
    }

    @Test
    void cleanupClaimRejectsAConcurrentBuildRegistration() {
        RagBranchIndex branchIndex = new RagBranchIndex(
                project, "feature/expired", RagBranchIndexKind.TRANSIENT);
        branchIndex.setId(10L);
        branchIndex.setCleanupClaimToken("cleanup-owner");
        when(operationRepository.findByProjectIdAndOperationKey(eq(42L), anyString()))
                .thenReturn(Optional.empty());
        when(branchIndexRepository.findByProjectIdAndBranchNameForUpdate(
                42L, "feature/expired"))
                .thenReturn(Optional.of(branchIndex));

        assertThatThrownBy(() -> service.registerBuild(
                project,
                "feature/expired",
                RagBranchIndexKind.TRANSIENT,
                "revision-99",
                "revision-100",
                "representation"))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("cleanup owns");

        verify(branchIndexRepository, never()).save(any());
        verifyNoInteractions(generationRepository);
    }
}
