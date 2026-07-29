package org.rostilos.codecrow.analysisengine.service.branch;

import okhttp3.OkHttpClient;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.analysisengine.processor.VcsRepoInfoImpl;
import org.rostilos.codecrow.analysisengine.service.BranchArchiveService;
import org.rostilos.codecrow.analysisengine.service.VcsFileRetrievalPolicy;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsOperationsService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchIssueRepository;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchRepository;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisIssueRepository;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;
import org.rostilos.codecrow.filecontent.persistence.BranchFileRepository;
import org.rostilos.codecrow.filecontent.service.FileSnapshotService;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;

import java.io.IOException;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class BranchFileOperationsServiceTest {

    @Mock private BranchFileRepository branchFileRepository;
    @Mock private BranchRepository branchRepository;
    @Mock private BranchIssueRepository branchIssueRepository;
    @Mock private CodeAnalysisIssueRepository codeAnalysisIssueRepository;
    @Mock private CodeAnalysisRepository codeAnalysisRepository;
    @Mock private VcsServiceFactory vcsServiceFactory;
    @Mock private VcsClientProvider vcsClientProvider;
    @Mock private FileSnapshotService fileSnapshotService;
    @Mock private BranchArchiveService branchArchiveService;
    @Mock private VcsFileRetrievalPolicy fileRetrievalPolicy;
    @Mock private Project project;
    @Mock private VcsConnection vcsConnection;
    @Mock private VcsRepoInfo vcsRepoInfo;
    @Mock private VcsOperationsService operationsService;
    @Mock private OkHttpClient httpClient;

    private BranchFileOperationsService service;

    @BeforeEach
    void setUp() {
        service = new BranchFileOperationsService(
                branchFileRepository,
                branchRepository,
                branchIssueRepository,
                codeAnalysisIssueRepository,
                codeAnalysisRepository,
                vcsServiceFactory,
                vcsClientProvider,
                fileSnapshotService,
                branchArchiveService,
                fileRetrievalPolicy);
    }

    @Test
    void preservesArchiveFailureWithoutEnablingLargePerFileFallback() throws Exception {
        Set<String> requestedFiles = new LinkedHashSet<>();
        for (int i = 0; i < 26; i++) {
            requestedFiles.add("src/File" + i + ".java");
        }
        VcsRepoInfoImpl repoInfo = new VcsRepoInfoImpl(vcsConnection, "workspace", "repo");
        when(branchArchiveService.downloadSnapshot(
                vcsConnection, "workspace", "repo", "commit", requestedFiles))
                .thenThrow(new IOException("archive unavailable"));
        when(fileRetrievalPolicy.allowPerFileFallback(26)).thenReturn(false);

        BranchFileOperationsService.BranchFileSnapshot snapshot =
                service.downloadBranchFileSnapshot(repoInfo, "commit", requestedFiles);

        assertThat(snapshot.archiveAvailable()).isFalse();
        assertThat(snapshot.allowPerFileFallback()).isFalse();
        assertThat(snapshot.diagnostic()).contains("archive unavailable");
    }

    @Test
    void usesArchivePathPresenceWithoutAnyProviderExistenceCalls() {
        configureProjectRepository();
        when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                .thenReturn(Optional.empty());
        when(branchFileRepository.findByProjectIdAndBranchNameAndFilePath(
                anyLong(), anyString(), anyString())).thenReturn(Optional.empty());
        when(codeAnalysisIssueRepository.findByProjectIdAndFilePath(anyLong(), anyString()))
                .thenReturn(List.of());

        BranchFileOperationsService.BranchFileSnapshot snapshot =
                BranchFileOperationsService.BranchFileSnapshot.fromArchive(
                        new BranchArchiveService.ArchiveSnapshot(
                                Map.of("src/Text.java", "class Text {}"),
                                Set.of("src/Text.java", "assets/logo.png")));

        Set<String> existing = service.updateBranchFiles(
                new LinkedHashSet<>(List.of(
                        "src/Text.java", "assets/logo.png", "src/Deleted.java")),
                project,
                "main",
                snapshot);

        assertThat(existing).containsExactlyInAnyOrder("src/Text.java", "assets/logo.png");
        verify(branchFileRepository, org.mockito.Mockito.times(2)).save(any());
        verifyNoInteractions(vcsServiceFactory, vcsClientProvider, operationsService);
    }

    @Test
    void stopsPerFileExistenceChecksAfterFirstProviderFailure() throws Exception {
        configureProjectRepository();
        when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                .thenReturn(Optional.empty());
        when(branchFileRepository.findByProjectIdAndBranchNameAndFilePath(
                anyLong(), anyString(), anyString())).thenReturn(Optional.empty());
        when(codeAnalysisIssueRepository.findByProjectIdAndFilePath(anyLong(), anyString()))
                .thenReturn(List.of());
        when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);
        when(vcsRepoInfo.getVcsConnection()).thenReturn(vcsConnection);
        when(vcsRepoInfo.getRepoWorkspace()).thenReturn("workspace");
        when(vcsRepoInfo.getRepoSlug()).thenReturn("repo");
        when(vcsServiceFactory.getOperationsService(EVcsProvider.BITBUCKET_CLOUD))
                .thenReturn(operationsService);
        when(vcsClientProvider.getHttpClient(vcsConnection)).thenReturn(httpClient);
        when(operationsService.checkFileExistsInBranch(
                httpClient, "workspace", "repo", "main", "src/A.java"))
                .thenThrow(new IOException("Unexpected response 429"));

        BranchFileOperationsService.BranchFileSnapshot snapshot =
                BranchFileOperationsService.BranchFileSnapshot.unavailable(
                        true, "archive unavailable");
        Set<String> existing = service.updateBranchFiles(
                new LinkedHashSet<>(List.of("src/A.java", "src/B.java")),
                project,
                "main",
                snapshot);

        assertThat(existing).containsExactlyInAnyOrder("src/A.java", "src/B.java");
        assertThat(snapshot.allowContentApiFallback()).isFalse();
        verify(operationsService).checkFileExistsInBranch(
                httpClient, "workspace", "repo", "main", "src/A.java");
        verify(operationsService, never()).checkFileExistsInBranch(
                httpClient, "workspace", "repo", "main", "src/B.java");
    }

    private void configureProjectRepository() {
        when(project.getId()).thenReturn(1L);
        when(project.getEffectiveVcsRepoInfo()).thenReturn(vcsRepoInfo);
    }
}
