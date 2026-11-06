package org.rostilos.codecrow.analysisengine.service;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.EnumSource;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.vcs.*;
import org.rostilos.codecrow.plugins.*;
import org.rostilos.codecrow.vcsclient.*;

import java.io.IOException;
import java.util.*;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

class ReviewCheckServiceTest {
    @ParameterizedTest
    @EnumSource(EVcsProvider.class)
    void mergesSecurityFindingsUsingOnlyTheProjectsPinnedSources(EVcsProvider provider) throws Exception {
        Project project = mock(Project.class);
        var configuration = new ProjectConfig();
        when(project.getEffectiveConfig()).thenReturn(configuration);
        VcsRepoInfo repository = mock(VcsRepoInfo.class);
        VcsConnection connection = mock(VcsConnection.class);
        when(project.getEffectiveVcsRepoInfo()).thenReturn(repository);
        when(repository.getVcsConnection()).thenReturn(connection);
        when(repository.getRepoWorkspace()).thenReturn("tenant-owner");
        when(repository.getRepoSlug()).thenReturn("tenant-repo");
        VcsClientProvider clients = mock(VcsClientProvider.class);
        VcsClient client = mock(VcsClient.class);
        when(clients.getClient(connection)).thenReturn(client);
        when(client.getFileContent("tenant-owner", "tenant-repo", "composer.lock", "head")).thenReturn("source");
        when(client.getMergeBase("tenant-owner", "tenant-repo", "base", "head")).thenReturn("merge-base");
        AiAnalysisRequest request = mock(AiAnalysisRequest.class);
        when(request.getBaseCommitHash()).thenReturn("base");
        when(request.getCurrentCommitHash()).thenReturn("head");
        when(request.getProposedTreeChangedFiles()).thenReturn(List.of("composer.lock"));
        ReviewCheckPlugin plugin = new ReviewCheckPlugin() {
            public PluginDescriptor descriptor() { return descriptorForTest(); }
            public Result check(Context context) throws IOException {
                assertThat(context.baseRevision()).isEqualTo("merge-base");
                assertThat(context.source().read("head", "composer.lock")).isEqualTo("source");
                assertThatThrownBy(() -> context.source().read("another-revision", "composer.lock")).isInstanceOf(IOException.class);
                assertThatThrownBy(() -> context.source().read("head", "../another-tenant")).isInstanceOf(IOException.class);
                return new Result(List.of(new Finding("composer.lock", 3, "HIGH", "Dependency advisory", "reason", "update", "snippet")), List.of("audited"));
            }
        };
        Map<String,Object> original = Map.of("issues", List.of(Map.of("title","existing", "reviewCheckId", "spoofed")), "comment", "review");
        var events = new ArrayList<Map<String,Object>>();
        var result = new ReviewCheckService(clients,List.of(plugin)).enrich(project, request, original, events::add);
        assertThat((List<?>)result.get("issues")).hasSize(2);
        assertThat((List<?>)original.get("issues")).hasSize(1);
        assertThat(((Map<?,?>)((List<?>)result.get("issues")).get(1)).get("category")).isEqualTo("SECURITY");
        assertThat(((Map<?,?>)((List<?>)result.get("issues")).get(1)).get("reviewCheckId")).isEqualTo("test-check");
        assertThat(((Map<?,?>)((List<?>)result.get("issues")).get(0)).get("reviewCheckId")).isNull();
        verify(client).getFileContent("tenant-owner", "tenant-repo", "composer.lock", "head");
        verify(client).getMergeBase("tenant-owner", "tenant-repo", "base", "head");
        verifyNoMoreInteractions(client);
        assertThat(events).hasSize(1);
    }

    @Test
    void noInstalledPluginsOrExplicitDisableLeaveReviewAvailable() throws Exception {
        var project = mock(Project.class);
        var configuration = new ProjectConfig();
        var clients = mock(VcsClientProvider.class);
        var request = mock(AiAnalysisRequest.class);
        var original = Map.<String,Object>of("issues", List.of(Map.of("title", "existing")));
        var events = new ArrayList<Map<String,Object>>();
        assertThat(new ReviewCheckService(clients,List.of()).enrich(project,request,original,events::add).get("issues"))
                .isEqualTo(original.get("issues"));
        var plugin = mock(ReviewCheckPlugin.class);
        when(plugin.descriptor()).thenReturn(descriptorForTest());
        configuration.setReviewChecks(Map.of("test-check",false));
        when(project.getEffectiveConfig()).thenReturn(configuration);
        new ReviewCheckService(clients,List.of(plugin)).enrich(project,request,original,events::add);
        verify(plugin,never()).check(any());
        verifyNoInteractions(clients);
        assertThat(events).hasSize(2);
    }

    private static PluginDescriptor descriptorForTest() {
        return new PluginDescriptor("test-check", PluginKind.DOMAIN, List.of(), List.of(PluginCapability.REVIEW_CHECK), DetectionRules.empty(), Map.of());
    }
}
