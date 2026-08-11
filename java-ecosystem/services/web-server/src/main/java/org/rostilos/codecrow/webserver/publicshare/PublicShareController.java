package org.rostilos.codecrow.webserver.publicshare;

import org.rostilos.codecrow.publicshare.api.ResolvedPublicShare;
import org.rostilos.codecrow.publicshare.service.PublicShareLinkService;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;
import java.util.function.Function;
import java.util.stream.Collectors;

@RestController
@RequestMapping("/api/public/shares")
public class PublicShareController {

    private final PublicShareLinkService shareLinkService;
    private final Map<String, PublicShareResourceProvider> providers;

    public PublicShareController(PublicShareLinkService shareLinkService,
                                 List<PublicShareResourceProvider> providers) {
        this.shareLinkService = shareLinkService;
        this.providers = providers.stream().collect(Collectors.toUnmodifiableMap(
                PublicShareResourceProvider::resourceType,
                Function.identity()
        ));
    }

    @PostMapping("/resolve")
    public ResponseEntity<?> resolvePublicPreview(@RequestBody PublicShareResolveRequest request,
                                                  Authentication authentication) {
        String token = request == null ? null : request.token();
        return shareLinkService.resolve(token)
                .flatMap(share -> resolvePreview(share, authentication))
                .map(this::ok)
                .orElseGet(this::notFound);
    }

    private ResponseEntity<?> ok(Object body) {
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .header("Referrer-Policy", "no-referrer")
                .body(body);
    }

    private ResponseEntity<?> notFound() {
        return ResponseEntity.status(404)
                .cacheControl(CacheControl.noStore())
                .header("Referrer-Policy", "no-referrer")
                .build();
    }

    private java.util.Optional<PublicSharePreviewResponse> resolvePreview(
            ResolvedPublicShare share,
            Authentication authentication) {
        PublicShareResourceProvider provider = providers.get(share.resourceType());
        if (provider == null) {
            return java.util.Optional.empty();
        }
        return provider.getPublicPreview(share.resourceKey())
                .map(content -> new PublicSharePreviewResponse(
                        share.resourceType(),
                        content,
                        provider.getAuthorizedPath(share.resourceKey(), authentication).orElse(null)
                ));
    }
}
