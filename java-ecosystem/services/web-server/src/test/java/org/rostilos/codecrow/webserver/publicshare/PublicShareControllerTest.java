package org.rostilos.codecrow.webserver.publicshare;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.publicshare.api.ResolvedPublicShare;
import org.rostilos.codecrow.publicshare.service.PublicShareLinkService;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;

import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.doReturn;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class PublicShareControllerTest {

    @Test
    void resolvesThroughTheRegisteredSanitizingProvider() {
        PublicShareLinkService links = mock(PublicShareLinkService.class);
        PublicShareResourceProvider provider = mock(PublicShareResourceProvider.class);
        Authentication authentication = mock(Authentication.class);
        when(provider.resourceType()).thenReturn("safe-preview");
        doReturn(Optional.of(Map.of("title", "Shared content")))
                .when(provider).getPublicPreview("internal-9");
        when(provider.getAuthorizedPath("internal-9", authentication))
                .thenReturn(Optional.of("/dashboard/acme/projects/shop?prNumber=9&subTab=qa-doc"));
        when(links.resolve("ccs_public-token"))
                .thenReturn(Optional.of(new ResolvedPublicShare("safe-preview", "internal-9")));
        PublicShareController controller = new PublicShareController(links, List.of(provider));

        ResponseEntity<?> response = controller.resolvePublicPreview(
                new PublicShareResolveRequest("ccs_public-token"),
                authentication
        );

        assertThat(response.getStatusCode().value()).isEqualTo(200);
        assertThat(response.getBody()).isEqualTo(new PublicSharePreviewResponse(
                "safe-preview",
                Map.of("title", "Shared content"),
                "/dashboard/acme/projects/shop?prNumber=9&subTab=qa-doc"
        ));
        assertThat(response.getHeaders().getCacheControl()).isEqualTo("no-store");
        assertThat(response.getHeaders().getFirst("Referrer-Policy")).isEqualTo("no-referrer");
    }

    @Test
    void usesTheSameNotFoundResponseForInvalidAndUnsupportedTokens() {
        PublicShareLinkService links = mock(PublicShareLinkService.class);
        PublicShareResourceProvider provider = mock(PublicShareResourceProvider.class);
        when(provider.resourceType()).thenReturn("safe-preview");
        when(links.resolve("invalid")).thenReturn(Optional.empty());
        PublicShareController controller = new PublicShareController(links, List.of(provider));

        assertThat(controller.resolvePublicPreview(new PublicShareResolveRequest("invalid"), null)
                .getStatusCode().value()).isEqualTo(404);
        verify(provider, never()).getPublicPreview(org.mockito.ArgumentMatchers.anyString());
    }
}
