package org.rostilos.codecrow.pipelineagent.generic.webhookhandler;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.EnumSource;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.model.vcs.*;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsRepoBindingRepository;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import java.util.List;
import java.util.Optional;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

class WebhookProjectResolverTest {
    final VcsRepoBindingRepository bindings = mock(VcsRepoBindingRepository.class);
    final ProjectRepository projects = mock(ProjectRepository.class);
    final TokenEncryptionService encryption = mock(TokenEncryptionService.class);
    final WebhookProjectResolver resolver = new WebhookProjectResolver(bindings, projects, encryption);

    VcsRepoBinding binding(long projectId, long workspaceId, String token) throws Exception {
        var workspace = mock(Workspace.class); when(workspace.getId()).thenReturn(workspaceId);
        var project = mock(Project.class); when(project.getId()).thenReturn(projectId);
        when(project.getWorkspace()).thenReturn(workspace); when(project.getAuthToken()).thenReturn(token);
        when(encryption.decrypt(token)).thenReturn(token);
        var connection = new VcsConnection(); connection.setWorkspace(workspace);
        var binding = new VcsRepoBinding(); binding.setProject(project); binding.setWorkspace(workspace);
        binding.setVcsConnection(connection);
        when(projects.findByIdWithFullDetails(projectId)).thenReturn(Optional.of(project));
        return binding;
    }
    @ParameterizedTest @EnumSource(value=EVcsProvider.class, names={"GITHUB", "GITLAB", "BITBUCKET_CLOUD"})
    void overlappingIdsAcrossConnectionsAndWorkspacesRouteOnlyByAuthentication(EVcsProvider provider) throws Exception {
        var one = binding(1, 10, "tenant-a"); var two = binding(2, 20, "tenant-b");
        when(bindings.findWebhookCandidates(provider, "42", "repo", "owner")).thenReturn(List.of(one, two));
        assertThat(resolver.findAuthenticatedProject(provider, "42", "repo", "owner",
                p -> resolver.validateWebhookAuth(p, "tenant-b"))).contains(two.getProject());
        assertThat(resolver.findAuthenticatedProject(provider, "42", "repo", "owner",
                p -> resolver.validateWebhookAuth(p, "wrong"))).isEmpty();
    }
    @Test void sharedSignatureCannotArbitrarilyPickAnIndependentWorkspace() throws Exception {
        var one = binding(1, 10, "one"); var two = binding(2, 20, "two");
        when(bindings.findWebhookCandidates(EVcsProvider.GITHUB, "42", "repo", "owner")).thenReturn(List.of(one, two));
        assertThatThrownBy(() -> resolver.findAuthenticatedProject(EVcsProvider.GITHUB, "42", "repo", "owner", p -> true))
                .isInstanceOf(IllegalArgumentException.class).hasMessageContaining("multiple authenticated projects");
    }
    @Test void inconsistentBindingNeverAuthenticatesAcrossTenantBoundary() throws Exception {
        var one = binding(1, 10, "one"); var other = binding(2, 20, "two");
        one.setVcsConnection(other.getVcsConnection());
        when(bindings.findWebhookCandidates(EVcsProvider.GITLAB, "42", "repo", "owner")).thenReturn(List.of(one));
        assertThat(resolver.findAuthenticatedProject(EVcsProvider.GITLAB, "42", "repo", "owner", p -> true)).isEmpty();
        verify(projects, never()).findByIdWithFullDetails(1L);
    }
    @Test void legacyPlaintextAndEncryptedTokensRemainCompatibleButBlankTokensDoNot() throws Exception {
        var one = binding(1, 10, "legacy");
        when(encryption.decrypt("legacy")).thenThrow(new IllegalArgumentException("not encrypted"));
        assertThat(resolver.validateWebhookAuth(one.getProject(), "legacy")).isTrue();
        assertThat(resolver.validateWebhookAuth(one.getProject(), null)).isFalse();
        when(one.getProject().getAuthToken()).thenReturn("");
        assertThat(resolver.validateWebhookAuth(one.getProject(), "")).isFalse();
    }
}
