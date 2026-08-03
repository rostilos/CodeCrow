package org.rostilos.codecrow.vcsclient.gitlab;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.dto.admin.GitLabSettingsDTO;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class GitLabOAuthProviderTest {

    @Test
    void bindsSiteCredentialsToTheirConfiguredIssuer() {
        GitLabOAuthProvider provider = GitLabOAuthProvider.from(
                new GitLabSettingsDTO(
                        "client-id",
                        "client-secret",
                        "HTTPS://GitLab.Example:443/root/api/v4/"));

        assertThat(provider.instanceBaseUrl())
                .isEqualTo("https://gitlab.example/root");
        assertThat(provider.requireIssuer("https://gitlab.example/root/"))
                .isSameAs(provider);
    }

    @Test
    void rejectsAConnectionFromAnotherIssuer() {
        GitLabOAuthProvider provider = GitLabOAuthProvider.from(
                new GitLabSettingsDTO(
                        "client-id",
                        "client-secret",
                        "https://gitlab.example"));
        VcsConnection connection = new VcsConnection();
        connection.setConfiguration(
                new org.rostilos.codecrow.core.model.vcs.config.gitlab.GitLabConfig(
                        null,
                        "group",
                        null,
                        "https://attacker.example"));

        assertThatThrownBy(() -> provider.requireConnectionIssuer(connection))
                .isInstanceOf(GitLabOAuthConfigurationException.class)
                .hasMessageContaining("https://attacker.example")
                .hasMessageContaining("https://gitlab.example");
    }

    @Test
    void legacyConnectionWithoutConfigurationUsesGitLabCom() {
        GitLabOAuthProvider provider = GitLabOAuthProvider.from(
                new GitLabSettingsDTO(
                        "client-id",
                        "client-secret",
                        ""));

        assertThat(provider.requireConnectionIssuer(new VcsConnection()))
                .isSameAs(provider);
        assertThat(provider.instanceBaseUrl()).isEqualTo("https://gitlab.com");
    }

    @Test
    void rejectsUnsafeOrIncompleteProviderSettings() {
        assertThatThrownBy(() -> GitLabOAuthProvider.from(
                new GitLabSettingsDTO(
                        "client-id",
                        "client-secret",
                        "https://user@gitlab.example?target=elsewhere")))
                .isInstanceOf(GitLabOAuthConfigurationException.class)
                .hasMessageContaining("must not contain");

        assertThatThrownBy(() -> GitLabOAuthProvider.from(
                new GitLabSettingsDTO(
                        "client-id",
                        "",
                        "https://gitlab.example")))
                .isInstanceOf(GitLabOAuthConfigurationException.class)
                .hasMessageContaining("credentials are not configured");

        assertThatThrownBy(() -> GitLabOAuthProvider.from(
                new GitLabSettingsDTO(
                        "client-id",
                        "client-secret",
                        "ftp://gitlab.example")))
                .isInstanceOf(GitLabOAuthConfigurationException.class)
                .hasMessageContaining("must use HTTP or HTTPS");
    }
}
