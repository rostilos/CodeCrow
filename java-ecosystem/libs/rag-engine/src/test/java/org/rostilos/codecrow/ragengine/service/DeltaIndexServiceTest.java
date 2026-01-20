package org.rostilos.codecrow.ragengine.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.ragengine.client.RagPipelineClient;

import java.io.IOException;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class DeltaIndexServiceTest {

    @Mock
    private RagPipelineClient ragPipelineClient;

    @Mock
    private Project testProject;

    @Mock
    private VcsConnection testConnection;

    private DeltaIndexService service;

    @BeforeEach
    void setUp() {
        service = new DeltaIndexService(ragPipelineClient);
        lenient().when(testProject.getId()).thenReturn(1L);
        lenient().when(testConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);
    }

    @Test
    void testCreateOrUpdateDeltaIndex_Success() throws IOException {
        Map<String, Object> mockResponse = Map.of(
                "collection_name", "test-collection",
                "file_count", 10,
                "chunk_count", 50,
                "base_commit_hash", "base123"
        );
        
        when(ragPipelineClient.createDeltaIndex(
                anyString(), anyString(), anyString(), anyString(), 
                anyString(), anyString(), anyString()))
                .thenReturn(mockResponse);

        Map<String, Object> result = service.createOrUpdateDeltaIndex(
                testProject,
                testConnection,
                "workspace",
                "repo",
                "feature",
                "main",
                "commit123",
                "diff content"
        );

        assertThat(result).containsEntry("collectionName", "test-collection");
        assertThat(result).containsEntry("fileCount", 10);
        assertThat(result).containsEntry("chunkCount", 50);
        assertThat(result).containsEntry("baseCommitHash", "base123");
    }

    @Test
    void testCreateOrUpdateDeltaIndex_WithNullValues() throws IOException {
        Map<String, Object> mockResponse = Map.of();
        
        when(ragPipelineClient.createDeltaIndex(
                anyString(), anyString(), anyString(), anyString(), 
                anyString(), anyString(), anyString()))
                .thenReturn(mockResponse);

        Map<String, Object> result = service.createOrUpdateDeltaIndex(
                testProject,
                testConnection,
                "workspace",
                "repo",
                "feature",
                "main",
                "commit123",
                "diff"
        );

        assertThat(result).containsEntry("collectionName", "");
        assertThat(result).containsEntry("fileCount", 0);
        assertThat(result).containsEntry("chunkCount", 0);
        assertThat(result).containsEntry("baseCommitHash", "");
    }

    @Test
    void testCreateOrUpdateDeltaIndex_ThrowsOnIOException() throws IOException {
        when(ragPipelineClient.createDeltaIndex(
                anyString(), anyString(), anyString(), anyString(), 
                anyString(), anyString(), anyString()))
                .thenThrow(new IOException("Network error"));

        assertThatThrownBy(() -> service.createOrUpdateDeltaIndex(
                testProject,
                testConnection,
                "workspace",
                "repo",
                "feature",
                "main",
                "commit123",
                "diff"
        ))
                .isInstanceOf(RuntimeException.class)
                .hasMessageContaining("Failed to create delta index")
                .hasCauseInstanceOf(IOException.class);
    }

    @Test
    void testConstructor() {
        DeltaIndexService newService = new DeltaIndexService(ragPipelineClient);
        assertThat(newService).isNotNull();
    }
}
