package org.rostilos.codecrow.analysisengine.service;

import okhttp3.OkHttpClient;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsOperationsService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.pullrequest.PullRequest;
import org.rostilos.codecrow.core.model.pullrequest.PullRequestState;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;
import org.rostilos.codecrow.core.persistence.repository.pullrequest.PullRequestRepository;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;

import java.io.IOException;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.function.Consumer;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
@DisplayName("PullRequestStatusSyncService")
class PullRequestStatusSyncServiceTest {

    @Mock private PullRequestRepository pullRequestRepository;
    @Mock private VcsClientProvider vcsClientProvider;
    @Mock private VcsServiceFactory vcsServiceFactory;
    @Mock private VcsOperationsService operationsService;
    @Mock private Project project;
    @Mock private VcsConnection vcsConnection;
    @Mock private OkHttpClient httpClient;

    private PullRequestStatusSyncService service;

    @BeforeEach
    void setUp() {
        service = new PullRequestStatusSyncService(
                pullRequestRepository,
                vcsClientProvider,
                vcsServiceFactory);
    }

    @Test
    @DisplayName("should repair stale open PR states from VCS across the whole project")
    void shouldRepairStaleOpenPrStatesFromVcsAcrossWholeProject() throws IOException {
        Consumer<Map<String, Object>> consumer = mock(Consumer.class);
        PullRequest merged = pullRequest(11L);
        PullRequest stillOpen = pullRequest(12L);
        PullRequest declined = pullRequest(13L);
        PullRequest unknown = pullRequest(14L);

        when(project.getId()).thenReturn(1L);
        when(pullRequestRepository.findByProject_IdAndState(1L, PullRequestState.OPEN))
                .thenReturn(List.of(merged, stillOpen, declined, unknown));
        mockVcsInfo();

        when(operationsService.getPullRequestState(httpClient, "ws", "repo", 11L))
                .thenReturn(Optional.of(PullRequestState.MERGED));
        when(operationsService.getPullRequestState(httpClient, "ws", "repo", 12L))
                .thenReturn(Optional.of(PullRequestState.OPEN));
        when(operationsService.getPullRequestState(httpClient, "ws", "repo", 13L))
                .thenReturn(Optional.of(PullRequestState.DECLINED));
        when(operationsService.getPullRequestState(httpClient, "ws", "repo", 14L))
                .thenReturn(Optional.empty());

        PullRequestStatusSyncService.SyncResult result =
                service.syncOpenPullRequestStates(project, consumer);

        assertThat(result.checked()).isEqualTo(4);
        assertThat(result.stillOpen()).isEqualTo(1);
        assertThat(result.markedMerged()).isEqualTo(1);
        assertThat(result.markedDeclined()).isEqualTo(1);
        assertThat(result.failed()).isEqualTo(1);
        assertThat(merged.getState()).isEqualTo(PullRequestState.MERGED);
        assertThat(stillOpen.getState()).isEqualTo(PullRequestState.OPEN);
        assertThat(declined.getState()).isEqualTo(PullRequestState.DECLINED);
        verify(pullRequestRepository).save(merged);
        verify(pullRequestRepository).save(declined);
        verify(pullRequestRepository, never()).save(stillOpen);
        verify(pullRequestRepository, never()).save(unknown);
    }

    private void mockVcsInfo() {
        VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
        when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
        when(repoInfo.getVcsConnection()).thenReturn(vcsConnection);
        when(repoInfo.getRepoWorkspace()).thenReturn("ws");
        when(repoInfo.getRepoSlug()).thenReturn("repo");
        when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.GITHUB);
        when(vcsClientProvider.getHttpClient(vcsConnection)).thenReturn(httpClient);
        when(vcsServiceFactory.getOperationsService(EVcsProvider.GITHUB)).thenReturn(operationsService);
    }

    private PullRequest pullRequest(Long prNumber) {
        PullRequest pullRequest = new PullRequest();
        pullRequest.setPrNumber(prNumber);
        pullRequest.setState(PullRequestState.OPEN);
        return pullRequest;
    }
}
