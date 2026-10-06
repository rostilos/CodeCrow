package org.rostilos.codecrow.pipelineagent.generic.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.EnumSource;
import org.rostilos.codecrow.core.dto.admin.GitHubSettingsDTO;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.*;
import org.rostilos.codecrow.core.service.SiteSettingsProvider;
import org.rostilos.codecrow.pipelineagent.generic.webhookhandler.WebhookProjectResolver;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.util.HexFormat;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

class NativeWebhookAuthenticatorTest {
    final WebhookProjectResolver resolver = mock(WebhookProjectResolver.class);
    final SiteSettingsProvider settings = mock(SiteSettingsProvider.class);
    final NativeWebhookAuthenticator auth = new NativeWebhookAuthenticator(resolver, settings);
    final Project project = mock(Project.class);
    final ObjectMapper mapper = new ObjectMapper();
    static String sign(byte[] body, String secret) throws Exception {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
        return "sha256=" + HexFormat.of().formatHex(mac.doFinal(body));
    }
    @ParameterizedTest @EnumSource(value=EVcsProvider.class, names={"GITHUB", "BITBUCKET_CLOUD"})
    void signatureUsesExactBytesAndRejectsMissingWrongOrMalformedCredentials(EVcsProvider provider) throws Exception {
        byte[] raw = "{\"name\":\"київ\", \"value\":1}".getBytes(StandardCharsets.UTF_8);
        when(resolver.webhookSecret(project)).thenReturn("project-secret");
        String signature = sign(raw, "project-secret");
        assertThat(auth.authenticate(project, provider, mapper.readTree(raw), raw, signature, signature, null)).isTrue();
        byte[] modified = "{\"name\":\"київ\", \"value\":2}".getBytes(StandardCharsets.UTF_8);
        for (String invalid : new String[]{null, "", "sha1=abcdef", "sha256=zz", sign(raw, "other-secret")}) {
            assertThat(auth.authenticate(project, provider, mapper.readTree(raw), raw, invalid, invalid, null)).isFalse();
        }
        assertThat(auth.authenticate(project, provider, mapper.readTree(modified), modified, signature, signature, null)).isFalse();
        when(resolver.webhookSecret(project)).thenReturn("");
        assertThat(auth.authenticate(project, provider, mapper.readTree(raw), raw, signature, signature, null)).isFalse();
    }
    @Test void gitlabRequiresProjectScopedNativeToken() throws Exception {
        when(resolver.validateWebhookAuth(project, "correct")).thenReturn(true);
        assertThat(auth.authenticate(project, EVcsProvider.GITLAB, mapper.readTree("{}"), new byte[0], null, null, "correct")).isTrue();
        assertThat(auth.authenticate(project, EVcsProvider.GITLAB, mapper.readTree("{}"), new byte[0], null, null, "wrong")).isFalse();
    }
    @ParameterizedTest @EnumSource(value=EVcsConnectionType.class, names={"APP", "GITHUB_APP"})
    void githubAppSecretIsBoundToSignedInstallation(EVcsConnectionType type) throws Exception {
        var repo = mock(VcsRepoInfo.class); var connection = mock(VcsConnection.class);
        when(project.getEffectiveVcsRepoInfo()).thenReturn(repo);
        when(repo.getVcsConnection()).thenReturn(connection);
        when(connection.getConnectionType()).thenReturn(type);
        when(connection.getInstallationId()).thenReturn("42");
        when(settings.getGitHubSettings()).thenReturn(new GitHubSettingsDTO("", "", "", "app-secret", "", "", ""));
        byte[] raw = "{\"installation\":{\"id\":42}}".getBytes(StandardCharsets.UTF_8);
        String signature = sign(raw, "app-secret");
        assertThat(auth.authenticate(project, EVcsProvider.GITHUB, mapper.readTree(raw), raw, signature, null, null)).isTrue();
        when(connection.getInstallationId()).thenReturn("43");
        assertThat(auth.authenticate(project, EVcsProvider.GITHUB, mapper.readTree(raw), raw, signature, null, null)).isFalse();
        when(connection.getInstallationId()).thenReturn("42");
        when(connection.getConnectionType()).thenReturn(EVcsConnectionType.REPOSITORY_TOKEN);
        assertThat(auth.authenticate(project, EVcsProvider.GITHUB, mapper.readTree(raw), raw, signature, null, null)).isFalse();
    }
}
