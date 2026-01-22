package org.rostilos.codecrow.analysisengine.service.vcs;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class VcsServiceFactoryTest {

    @Mock
    private VcsAiClientService githubAiService;

    @Mock
    private VcsAiClientService gitlabAiService;

    @Mock
    private VcsReportingService githubReportingService;

    @Mock
    private VcsReportingService gitlabReportingService;

    @Mock
    private VcsOperationsService githubOperationsService;

    @Mock
    private VcsOperationsService gitlabOperationsService;

    private VcsServiceFactory factory;

    @BeforeEach
    void setUp() {
        when(githubAiService.getProvider()).thenReturn(EVcsProvider.GITHUB);
        when(gitlabAiService.getProvider()).thenReturn(EVcsProvider.GITLAB);
        when(githubReportingService.getProvider()).thenReturn(EVcsProvider.GITHUB);
        when(gitlabReportingService.getProvider()).thenReturn(EVcsProvider.GITLAB);
        when(githubOperationsService.getProvider()).thenReturn(EVcsProvider.GITHUB);
        when(gitlabOperationsService.getProvider()).thenReturn(EVcsProvider.GITLAB);

        List<VcsAiClientService> aiServices = Arrays.asList(githubAiService, gitlabAiService);
        List<VcsReportingService> reportingServices = Arrays.asList(githubReportingService, gitlabReportingService);
        List<VcsOperationsService> operationsServices = Arrays.asList(githubOperationsService, gitlabOperationsService);

        factory = new VcsServiceFactory(aiServices, reportingServices, operationsServices);
    }

    @Test
    void testGetAiClientService_GitHub_ReturnsGitHubService() {
        VcsAiClientService result = factory.getAiClientService(EVcsProvider.GITHUB);

        assertThat(result).isEqualTo(githubAiService);
    }

    @Test
    void testGetAiClientService_GitLab_ReturnsGitLabService() {
        VcsAiClientService result = factory.getAiClientService(EVcsProvider.GITLAB);

        assertThat(result).isEqualTo(gitlabAiService);
    }

    @Test
    void testGetAiClientService_UnknownProvider_ThrowsException() {
        assertThatThrownBy(() -> factory.getAiClientService(EVcsProvider.BITBUCKET_CLOUD))
                .isInstanceOf(UnsupportedOperationException.class)
                .hasMessageContaining("No AI client service registered for provider: BITBUCKET_CLOUD");
    }

    @Test
    void testGetReportingService_GitHub_ReturnsGitHubService() {
        VcsReportingService result = factory.getReportingService(EVcsProvider.GITHUB);

        assertThat(result).isEqualTo(githubReportingService);
    }

    @Test
    void testGetReportingService_GitLab_ReturnsGitLabService() {
        VcsReportingService result = factory.getReportingService(EVcsProvider.GITLAB);

        assertThat(result).isEqualTo(gitlabReportingService);
    }

    @Test
    void testGetReportingService_UnknownProvider_ThrowsException() {
        assertThatThrownBy(() -> factory.getReportingService(EVcsProvider.BITBUCKET_CLOUD))
                .isInstanceOf(UnsupportedOperationException.class)
                .hasMessageContaining("No reporting service registered for provider: BITBUCKET_CLOUD");
    }

    @Test
    void testGetOperationsService_GitHub_ReturnsGitHubService() {
        VcsOperationsService result = factory.getOperationsService(EVcsProvider.GITHUB);

        assertThat(result).isEqualTo(githubOperationsService);
    }

    @Test
    void testGetOperationsService_GitLab_ReturnsGitLabService() {
        VcsOperationsService result = factory.getOperationsService(EVcsProvider.GITLAB);

        assertThat(result).isEqualTo(gitlabOperationsService);
    }

    @Test
    void testGetOperationsService_UnknownProvider_ThrowsException() {
        assertThatThrownBy(() -> factory.getOperationsService(EVcsProvider.BITBUCKET_CLOUD))
                .isInstanceOf(UnsupportedOperationException.class)
                .hasMessageContaining("No operations service registered for provider: BITBUCKET_CLOUD");
    }

    @Test
    void testFactoryWithEmptyLists_ThrowsExceptionForAnyProvider() {
        VcsServiceFactory emptyFactory = new VcsServiceFactory(
                Collections.emptyList(),
                Collections.emptyList(),
                Collections.emptyList()
        );

        assertThatThrownBy(() -> emptyFactory.getAiClientService(EVcsProvider.GITHUB))
                .isInstanceOf(UnsupportedOperationException.class);
        assertThatThrownBy(() -> emptyFactory.getReportingService(EVcsProvider.GITHUB))
                .isInstanceOf(UnsupportedOperationException.class);
        assertThatThrownBy(() -> emptyFactory.getOperationsService(EVcsProvider.GITHUB))
                .isInstanceOf(UnsupportedOperationException.class);
    }

    @Test
    void testFactoryWithOnlyGitHub_GitLabNotAvailable() {
        VcsServiceFactory githubOnlyFactory = new VcsServiceFactory(
                List.of(githubAiService),
                List.of(githubReportingService),
                List.of(githubOperationsService)
        );

        assertThat(githubOnlyFactory.getAiClientService(EVcsProvider.GITHUB))
                .isEqualTo(githubAiService);

        assertThatThrownBy(() -> githubOnlyFactory.getAiClientService(EVcsProvider.GITLAB))
                .isInstanceOf(UnsupportedOperationException.class)
                .hasMessageContaining("GITLAB");
    }

    @Test
    void testAllServicesForSameProvider_ReturnsConsistently() {
        VcsAiClientService aiService = factory.getAiClientService(EVcsProvider.GITHUB);
        VcsReportingService reportingService = factory.getReportingService(EVcsProvider.GITHUB);
        VcsOperationsService operationsService = factory.getOperationsService(EVcsProvider.GITHUB);

        assertThat(aiService.getProvider()).isEqualTo(EVcsProvider.GITHUB);
        assertThat(reportingService.getProvider()).isEqualTo(EVcsProvider.GITHUB);
        assertThat(operationsService.getProvider()).isEqualTo(EVcsProvider.GITHUB);
    }
}
