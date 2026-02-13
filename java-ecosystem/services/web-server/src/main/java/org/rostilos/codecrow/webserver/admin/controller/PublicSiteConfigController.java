package org.rostilos.codecrow.webserver.admin.controller;

import org.rostilos.codecrow.core.dto.admin.GoogleOAuthSettingsDTO;
import org.rostilos.codecrow.core.service.SiteSettingsProvider;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Public (unauthenticated) endpoint that exposes non-secret site configuration
 * needed by the frontend before a user logs in.
 * <p>
 * This allows the frontend to discover at <b>runtime</b> whether features like
 * Google OAuth are enabled, without requiring the value to be baked in at build time.
 */
@RestController
@RequestMapping("/api/public")
@CrossOrigin(origins = "*", maxAge = 3600)
public class PublicSiteConfigController {

    private final SiteSettingsProvider settingsProvider;

    public PublicSiteConfigController(SiteSettingsProvider settingsProvider) {
        this.settingsProvider = settingsProvider;
    }

    /**
     * Returns a subset of site configuration that is safe to expose publicly.
     * Currently includes:
     * <ul>
     *   <li>{@code googleClientId} — the Google OAuth 2.0 Client ID (not a secret)</li>
     *   <li>{@code vcsProviders} — boolean flags indicating which VCS providers are configured</li>
     * </ul>
     */
    @GetMapping("/site-config")
    public ResponseEntity<Map<String, Object>> getPublicConfig() {
        Map<String, Object> config = new LinkedHashMap<>();

        // Google OAuth Client ID is NOT a secret — it's embedded in the login page HTML
        // by Google's own documentation. Safe to expose publicly.
        GoogleOAuthSettingsDTO google = settingsProvider.getGoogleOAuthSettings();
        if (google != null && google.clientId() != null && !google.clientId().isBlank()) {
            config.put("googleClientId", google.clientId());
        }

        // VCS provider availability — only boolean flags, no secrets.
        // Tells the frontend which "Connect" buttons to show vs info banners.
        config.put("vcsProviders", settingsProvider.getVcsProviderAvailability());

        return ResponseEntity.ok(config);
    }
}
