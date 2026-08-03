package org.rostilos.codecrow.webserver.integration.dto.response;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.config.gitlab.GitLabConfig;

import static org.assertj.core.api.Assertions.assertThat;

class VcsConnectionDTOTest {

    @Test
    void exposesNormalizedSelfHostedGitLabBaseUrl() {
        VcsConnection connection = new VcsConnection();
        connection.setProviderType(EVcsProvider.GITLAB);
        connection.setConfiguration(new GitLabConfig(
                null, "team", null, "https://gitlab.example.com/api/v4/"));

        VcsConnectionDTO dto = VcsConnectionDTO.fromEntity(connection);

        assertThat(dto.baseUrl()).isEqualTo("https://gitlab.example.com");
    }

    @Test
    void defaultsLegacyGitLabConnectionToGitLabCom() {
        VcsConnection connection = new VcsConnection();
        connection.setProviderType(EVcsProvider.GITLAB);

        VcsConnectionDTO dto = VcsConnectionDTO.fromEntity(connection);

        assertThat(dto.baseUrl()).isEqualTo(GitLabConfig.DEFAULT_BASE_URL);
    }

    @Test
    void omitsBaseUrlForOtherProviders() {
        VcsConnection connection = new VcsConnection();
        connection.setProviderType(EVcsProvider.GITHUB);

        VcsConnectionDTO dto = VcsConnectionDTO.fromEntity(connection);

        assertThat(dto.baseUrl()).isNull();
    }
}
