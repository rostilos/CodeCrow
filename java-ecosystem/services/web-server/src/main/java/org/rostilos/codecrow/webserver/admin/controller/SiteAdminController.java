package org.rostilos.codecrow.webserver.admin.controller;

import org.rostilos.codecrow.core.dto.admin.ConfigurationStatusDTO;
import org.rostilos.codecrow.core.model.admin.ESiteSettingsGroup;
import org.rostilos.codecrow.security.annotations.IsSiteAdmin;
import org.rostilos.codecrow.webserver.admin.service.ISiteSettingsProvider;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/**
 * REST API for site-wide admin settings.
 * All endpoints require {@code ROLE_ADMIN} authority via {@link IsSiteAdmin}.
 */
@RestController
@RequestMapping("/api/admin/settings")
@CrossOrigin(origins = "*", maxAge = 3600)
public class SiteAdminController {

    private final ISiteSettingsProvider settingsProvider;

    public SiteAdminController(ISiteSettingsProvider settingsProvider) {
        this.settingsProvider = settingsProvider;
    }

    // ─────────── Configuration status (setup wizard) ───────────

    /**
     * Returns which settings groups are configured.
     * Used by the frontend setup wizard to show progress.
     * Available to any authenticated admin user.
     */
    @GetMapping("/status")
    @IsSiteAdmin
    public ResponseEntity<ConfigurationStatusDTO> getConfigurationStatus() {
        return ResponseEntity.ok(settingsProvider.getConfigurationStatus());
    }

    // ─────────── Per-group CRUD ───────────

    /**
     * Get current settings for a group (secrets are masked).
     */
    @GetMapping("/{group}")
    @IsSiteAdmin
    public ResponseEntity<Map<String, String>> getSettings(@PathVariable("group") ESiteSettingsGroup group) {
        return ResponseEntity.ok(settingsProvider.getSettingsGroupMasked(group));
    }

    /**
     * Update settings for a group. Masked secret values ("••••") are skipped
     * to avoid overwriting existing secrets when the admin didn't change them.
     */
    @PutMapping("/{group}")
    @IsSiteAdmin
    public ResponseEntity<Map<String, String>> updateSettings(
            @PathVariable("group") ESiteSettingsGroup group,
            @RequestBody Map<String, String> values) {
        settingsProvider.updateSettingsGroup(group, values);
        return ResponseEntity.ok(settingsProvider.getSettingsGroupMasked(group));
    }
}
