package org.rostilos.codecrow.webserver.publicshare;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.publicshare.api.ResolvedPublicShare;
import org.rostilos.codecrow.publicshare.service.PublicShareLinkService;
import org.springframework.http.ResponseEntity;

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
        when(provider.resourceType()).thenReturn("safe-preview");
        doReturn(Optional.of(Map.of("title", "Shared content")))
                .when(provider).getPublicPreview("internal-9");
        when(links.resolve("ccs_public-token"))
                .thenReturn(Optional.of(new ResolvedPublicShare("safe-preview", "internal-9")));
        PublicShareController controller = new PublicShareController(links, List.of(provider));

        ResponseEntity<?> response = controller.resolvePublicPreview(
                new PublicShareResolveRequest("ccs_public-token")
        );

        assertThat(response.getStatusCode().value()).isEqualTo(200);
        assertThat(response.getBody()).isEqualTo(Map.of("title", "Shared content"));
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

        assertThat(controller.resolvePublicPreview(new PublicShareResolveRequest("invalid"))
                .getStatusCode().value()).isEqualTo(404);
        verify(provider, never()).getPublicPreview(org.mockito.ArgumentMatchers.anyString());
    }
}
