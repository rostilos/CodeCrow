package org.rostilos.codecrow.ragengine.branch;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.analysisengine.service.BranchArchiveService;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.rag.RagBranchIndex;
import org.rostilos.codecrow.core.model.rag.RagBranchIndexGeneration;
import org.rostilos.codecrow.core.model.rag.RagBranchIndexKind;
import org.rostilos.codecrow.core.model.rag.RagIndexOperation;
import org.rostilos.codecrow.core.model.rag.RagIndexOperationStatus;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.ragengine.client.RagPipelineClient;
import org.rostilos.codecrow.ragengine.service.RagBranchIndexRegistryService;
import org.springframework.test.util.ReflectionTestUtils;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class BranchIndexGenerationBuildServiceTest {
    @Mock private BranchArchiveService archiveService;
    @Mock private RagPipelineClient pipelineClient;
    @Mock private RagBranchIndexRegistryService registryService;

    private BranchIndexGenerationBuildService service;
    private Project project;
    private RagBranchIndexGeneration generation;
    private RagIndexOperation operation;

    @BeforeEach
    void setUp() {
        service = new BranchIndexGenerationBuildService(
                archiveService, pipelineClient, registryService);
        project = new Project();
        ReflectionTestUtils.setField(project, "id", 42L);
        RagBranchIndex branchIndex = new RagBranchIndex(
                project, "develop", RagBranchIndexKind.DURABLE);
        branchIndex.setId(10L);
        generation = new RagBranchIndexGeneration(
                branchIndex, "develop-400", "opaque-generation-target",
                null, null, null);
        generation.setId(20L);
        operation = new RagIndexOperation(
                project, "develop", null, "develop-400", "operation-key");
        operation.setId(30L);
        operation.setGeneration(generation);
    }

    @Test
    void buildsPinnedSnapshotPublishesManifestAndRemovesTemporaryTree() throws Exception {
        when(registryService.registerBuild(
                project, "develop", RagBranchIndexKind.DURABLE,
                null, "develop-400", null))
                .thenReturn(new RagBranchIndexRegistryService.BuildRegistration(
                        generation.getBranchIndex(), generation, operation, false));
        when(pipelineClient.indexRepository(
                anyString(), eq("workspace"), eq("namespace"),
                eq("develop"), eq("develop-400"), eq(List.of("src/**")),
                eq(List.of("vendor/**")), eq("opaque-generation-target"),
                eq(false), eq(false)))
                .thenReturn(Map.of(
                        "generation_manifest_sha256", "manifest-400",
                        "document_count", 231,
                        "chunk_count", 400));
        when(registryService.publish(30L, "manifest-400", 231, 400))
                .thenAnswer(invocation -> {
                    generation.activate("manifest-400", 231, 400);
                    return generation;
                });
        ReflectionTestUtils.setField(project, "namespace", "namespace");
        var workspace = new org.rostilos.codecrow.core.model.workspace.Workspace();
        ReflectionTestUtils.setField(workspace, "name", "workspace");
        project.setWorkspace(workspace);

        Map<String, Object> result = service.build(
                project, new VcsConnection(), "provider-workspace", "repo",
                "develop", "develop-400", RagBranchIndexKind.DURABLE,
                List.of("src/**"), List.of("vendor/**"), 77L);

        assertThat(result).containsEntry(
                "generation_manifest_sha256", "manifest-400");
        verify(registryService).startBuild(30L, 77L, null);
        verify(registryService).publish(30L, "manifest-400", 231, 400);
        verify(pipelineClient).publishGenerationAliases(
                "workspace", "namespace", "develop", "develop-400",
                "opaque-generation-target", true, false);
        ArgumentCaptor<Path> snapshot = ArgumentCaptor.forClass(Path.class);
        verify(archiveService).downloadAndExtractSnapshotToDirectory(
                any(), eq("provider-workspace"), eq("repo"),
                eq("develop-400"), isNull(), snapshot.capture());
        assertThat(Files.exists(snapshot.getValue())).isFalse();
    }

    @Test
    void missingManifestFailsOperationAndDoesNotPublish() throws Exception {
        when(registryService.registerBuild(any(), anyString(), any(), isNull(),
                anyString(), isNull()))
                .thenReturn(new RagBranchIndexRegistryService.BuildRegistration(
                        generation.getBranchIndex(), generation, operation, false));
        when(pipelineClient.indexRepository(
                anyString(), any(), any(), any(), any(), any(), any(), any(),
                anyBoolean(), anyBoolean()))
                .thenReturn(Map.of("document_count", 231));
        var workspace = new org.rostilos.codecrow.core.model.workspace.Workspace();
        ReflectionTestUtils.setField(workspace, "name", "workspace");
        project.setWorkspace(workspace);
        project.setNamespace("namespace");

        assertThatThrownBy(() -> service.build(
                project, new VcsConnection(), "provider-workspace", "repo",
                "develop", "develop-400", RagBranchIndexKind.DURABLE,
                List.of(), List.of()))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("no manifest digest");

        verify(registryService).fail(30L,
                "RAG full branch generation has no manifest digest");
        verify(registryService, never()).publish(anyLong(), anyString(), anyInt(), anyInt());
    }

    @Test
    void successfulIdempotentOperationIsReusedWithoutProviderOrVectorCalls() throws Exception {
        operation.setStatus(RagIndexOperationStatus.SUCCEEDED);
        generation.activate("manifest-400", 231, 400);
        when(registryService.registerBuild(any(), anyString(), any(), isNull(),
                anyString(), isNull()))
                .thenReturn(new RagBranchIndexRegistryService.BuildRegistration(
                        generation.getBranchIndex(), generation, operation, true));

        Map<String, Object> result = service.build(
                project, new VcsConnection(), "provider-workspace", "repo",
                "develop", "develop-400", RagBranchIndexKind.DURABLE,
                List.of(), List.of());

        assertThat(result).containsEntry("status", "reused")
                .containsEntry("collection_target", "opaque-generation-target")
                .containsEntry("generation_manifest_sha256", "manifest-400");
        verifyNoInteractions(archiveService, pipelineClient);
    }

    @Test
    void explicitOperatorRefreshBuildsANewGenerationEvenForTheSameRevision() throws Exception {
        when(registryService.registerBuild(
                eq(project), eq("develop"), eq(RagBranchIndexKind.DURABLE),
                isNull(), eq("develop-400"), eq("operator-refresh:77")))
                .thenReturn(new RagBranchIndexRegistryService.BuildRegistration(
                        generation.getBranchIndex(), generation, operation, false));
        when(pipelineClient.indexRepository(
                anyString(), anyString(), anyString(), eq("develop"), eq("develop-400"),
                anyList(), anyList(), eq("opaque-generation-target"),
                eq(false), eq(false), any()))
                .thenReturn(Map.of(
                        "generation_manifest_sha256", "fresh-manifest",
                        "document_count", 231,
                        "chunk_count", 400));
        when(registryService.publish(30L, "fresh-manifest", 231, 400))
                .thenAnswer(invocation -> {
                    generation.activate("fresh-manifest", 231, 400);
                    return generation;
                });
        var workspace = new org.rostilos.codecrow.core.model.workspace.Workspace();
        ReflectionTestUtils.setField(workspace, "name", "workspace");
        project.setWorkspace(workspace);
        project.setNamespace("namespace");

        service.rebuild(project, new VcsConnection(), "provider-workspace", "repo",
                "develop", "develop-400", RagBranchIndexKind.DURABLE,
                List.of(), List.of(), 77L, ignored -> { });

        verify(archiveService).downloadAndExtractSnapshotToDirectory(
                any(), eq("provider-workspace"), eq("repo"), eq("develop-400"), isNull(), any());
        verify(registryService).publish(30L, "fresh-manifest", 231, 400);
        verify(pipelineClient).publishGenerationAliases(
                "workspace", "namespace", "develop", "develop-400",
                "opaque-generation-target", true, false);
    }

    @Test
    void staleCompletedGenerationDoesNotPublishReadableAliases() throws Exception {
        when(registryService.registerBuild(any(), anyString(), any(), isNull(),
                anyString(), isNull()))
                .thenReturn(new RagBranchIndexRegistryService.BuildRegistration(
                        generation.getBranchIndex(), generation, operation, false));
        when(pipelineClient.indexRepository(
                anyString(), anyString(), anyString(), anyString(), anyString(),
                anyList(), anyList(), anyString(), eq(false), eq(false)))
                .thenReturn(Map.of(
                        "generation_manifest_sha256", "manifest-400",
                        "document_count", 231,
                        "chunk_count", 400));
        generation.activate("manifest-400", 231, 400);
        generation.supersede();
        when(registryService.publish(30L, "manifest-400", 231, 400))
                .thenReturn(generation);
        var workspace = new org.rostilos.codecrow.core.model.workspace.Workspace();
        ReflectionTestUtils.setField(workspace, "name", "workspace");
        project.setWorkspace(workspace);
        project.setNamespace("namespace");

        service.build(project, new VcsConnection(), "provider-workspace", "repo",
                "develop", "develop-400", RagBranchIndexKind.DURABLE,
                List.of(), List.of());

        verify(pipelineClient, never()).publishGenerationAliases(
                anyString(), anyString(), anyString(), anyString(), anyString(),
                anyBoolean(), anyBoolean());
    }
}
