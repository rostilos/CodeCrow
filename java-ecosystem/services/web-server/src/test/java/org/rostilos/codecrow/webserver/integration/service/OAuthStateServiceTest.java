package org.rostilos.codecrow.webserver.integration.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.lang.reflect.Field;
import java.nio.charset.StandardCharsets;
import java.util.Base64;

import static org.assertj.core.api.Assertions.assertThat;

class OAuthStateServiceTest {

    private OAuthStateService service;

    @BeforeEach
    void setUp() throws Exception {
        service = new OAuthStateService();
        Field secretKey = OAuthStateService.class.getDeclaredField("secretKey");
        secretKey.setAccessible(true);
        secretKey.set(service, "test-only-state-signing-secret");
    }

    @Test
    void signedStateCarriesExactConnectionAndInstallation() {
        String state = service.generateState("github", 10L, 34L, 145918007L);

        OAuthStateService.OAuthStateData data = service.validateAndExtractState(state);

        assertThat(data).isNotNull();
        assertThat(data.providerId()).isEqualTo("github");
        assertThat(data.workspaceId()).isEqualTo(10L);
        assertThat(data.connectionId()).isEqualTo(34L);
        assertThat(data.installationId()).isEqualTo(145918007L);
    }

    @Test
    void installationIdCannotBeChangedWithoutInvalidatingState() {
        String state = service.generateState("github", 10L, 34L, 145918007L);
        String decoded = new String(
                Base64.getUrlDecoder().decode(state), StandardCharsets.UTF_8);
        String forged = decoded.replace(":145918007:", ":145874740:");
        String forgedState = Base64.getUrlEncoder().withoutPadding()
                .encodeToString(forged.getBytes(StandardCharsets.UTF_8));

        assertThat(service.validateAndExtractState(forgedState)).isNull();
    }

    @Test
    void signedStateCarriesExplicitInstallationFlowPurpose() {
        String state = service.generateState(
                "github", 10L, 34L, null, OAuthStateService.GITHUB_INSTALL_SELECT);

        OAuthStateService.OAuthStateData data = service.validateAndExtractState(state);

        assertThat(data).isNotNull();
        assertThat(data.connectionId()).isEqualTo(34L);
        assertThat(data.installationId()).isNull();
        assertThat(data.purpose()).isEqualTo(OAuthStateService.GITHUB_INSTALL_SELECT);
    }
}
