package org.rostilos.codecrow.webserver.project.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.EVcsSetupStatus;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsConnectionRepository;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.rostilos.codecrow.webserver.exception.IntegrationException;

import java.io.IOException;

import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class ProjectWebhookCleanupServiceTest {

    @Mock private VcsClientProvider vcsClientProvider;
    @Mock private VcsConnectionRepository vcsConnectionRepository;
    @Mock private VcsClient vcsClient;

    private ProjectWebhookCleanupService service;

    @BeforeEach
    void setUp() {
        service = new ProjectWebhookCleanupService(
                vcsClientProvider,
                vcsConnectionRepository);
    }

    @Test
    void deletesOnlyStoredWebhookFromExactBoundRepository() throws Exception {
        VcsRepoBinding binding = binding("org", "repo", "hook-for-this-project");
        when(vcsClientProvider.getClient(binding.getVcsConnection())).thenReturn(vcsClient);

        service.deleteProjectWebhook(binding);

        verify(vcsClient).deleteWebhook("org", "repo", "hook-for-this-project");
        verify(vcsClient, never()).listWebhooks("org", "repo");
    }

    @Test
    void repositoryTokenUsesItsExactConfiguredRepositoryPath() throws Exception {
        VcsRepoBinding binding = binding("ignored", "ignored", "17");
        binding.getVcsConnection().setConnectionType(EVcsConnectionType.REPOSITORY_TOKEN);
        binding.getVcsConnection().setRepositoryPath("group/subgroup/project");
        when(vcsClientProvider.getClient(binding.getVcsConnection())).thenReturn(vcsClient);

        service.deleteProjectWebhook(binding);

        verify(vcsClient).deleteWebhook("group/subgroup", "project", "17");
    }

    @Test
    void bindingWithoutRecordedWebhookDoesNotScanOrDeleteAnything() {
        VcsRepoBinding binding = binding("org", "repo", null);

        service.deleteProjectWebhook(binding);

        verifyNoInteractions(vcsClientProvider, vcsClient);
    }

    @Test
    void providerFailureKeepsCleanupRetryable() throws Exception {
        VcsRepoBinding binding = binding("org", "repo", "17");
        when(vcsClientProvider.getClient(binding.getVcsConnection())).thenReturn(vcsClient);
        when(vcsConnectionRepository.findByWorkspace_IdAndProviderType(
                10L, EVcsProvider.GITHUB)).thenReturn(java.util.List.of());
        org.mockito.Mockito.doThrow(new IOException("forbidden"))
                .when(vcsClient).deleteWebhook("org", "repo", "17");

        assertThatThrownBy(() -> service.deleteProjectWebhook(binding))
                .isInstanceOf(IntegrationException.class)
                .hasMessageContaining("kept so cleanup can be retried");
    }

    @Test
    void githubCleanupRetriesExactHookWithSameWorkspaceRepositoryOwnerConnection()
            throws Exception {
        VcsRepoBinding binding = binding("rostilos", "CodeCrow", "654711709");
        VcsConnection original = binding.getVcsConnection();
        original.setExternalWorkspaceSlug("CodeCrowAI-Team");

        VcsConnection matchingConnection = connection(20L, "rostilos");
        VcsClient matchingClient = org.mockito.Mockito.mock(VcsClient.class);
        when(vcsClientProvider.getClient(original)).thenReturn(vcsClient);
        when(vcsClientProvider.getClient(matchingConnection)).thenReturn(matchingClient);
        when(vcsConnectionRepository.findByWorkspace_IdAndProviderType(
                10L, EVcsProvider.GITHUB))
                .thenReturn(java.util.List.of(original, matchingConnection));
        org.mockito.Mockito.doThrow(new IOException("HTTP 403"))
                .when(vcsClient).deleteWebhook("rostilos", "CodeCrow", "654711709");

        service.deleteProjectWebhook(binding);

        verify(vcsClient).deleteWebhook("rostilos", "CodeCrow", "654711709");
        verify(matchingClient).deleteWebhook("rostilos", "CodeCrow", "654711709");
        verify(matchingClient, never()).listWebhooks("rostilos", "CodeCrow");
    }

    @Test
    void githubCleanupNeverUsesAnotherRepositoryOwnerAsFallback() throws Exception {
        VcsRepoBinding binding = binding("rostilos", "CodeCrow", "654711709");
        VcsConnection unrelatedConnection = connection(32L, "CodeCrowAI-Team");
        when(vcsClientProvider.getClient(binding.getVcsConnection())).thenReturn(vcsClient);
        when(vcsConnectionRepository.findByWorkspace_IdAndProviderType(
                10L, EVcsProvider.GITHUB))
                .thenReturn(java.util.List.of(unrelatedConnection));
        org.mockito.Mockito.doThrow(new IOException("HTTP 403"))
                .when(vcsClient).deleteWebhook("rostilos", "CodeCrow", "654711709");

        assertThatThrownBy(() -> service.deleteProjectWebhook(binding))
                .isInstanceOf(IntegrationException.class)
                .hasMessageContaining("kept so cleanup can be retried");

        verify(vcsClientProvider, never()).getClient(unrelatedConnection);
    }

    private VcsRepoBinding binding(String namespace, String slug, String webhookId) {
        VcsConnection connection = connection(12L, namespace);
        VcsRepoBinding binding = new VcsRepoBinding();
        binding.setId(9L);
        binding.setProvider(EVcsProvider.GITHUB);
        binding.setVcsConnection(connection);
        binding.setExternalNamespace(namespace);
        binding.setExternalRepoSlug(slug);
        binding.setWebhookId(webhookId);
        return binding;
    }

    private VcsConnection connection(Long id, String accountSlug) {
        Workspace workspace = org.mockito.Mockito.mock(Workspace.class);
        org.mockito.Mockito.lenient().when(workspace.getId()).thenReturn(10L);
        VcsConnection connection = new VcsConnection();
        connection.setId(id);
        connection.setWorkspace(workspace);
        connection.setProviderType(EVcsProvider.GITHUB);
        connection.setConnectionType(EVcsConnectionType.APP);
        connection.setSetupStatus(EVcsSetupStatus.CONNECTED);
        connection.setExternalWorkspaceSlug(accountSlug);
        return connection;
    }
}
