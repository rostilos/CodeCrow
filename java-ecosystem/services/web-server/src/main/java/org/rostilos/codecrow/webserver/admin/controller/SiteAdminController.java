package org.rostilos.codecrow.webserver.admin.controller;

import org.rostilos.codecrow.core.dto.admin.ConfigurationStatusDTO;
import org.rostilos.codecrow.core.model.admin.ESiteSettingsGroup;
import org.rostilos.codecrow.security.annotations.IsSiteAdmin;
import org.rostilos.codecrow.webserver.admin.service.ISiteSettingsProvider;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.io.Resource;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

import java.util.Map;

/**
 * REST API for site-wide admin settings.
 * All endpoints require {@code ROLE_ADMIN} authority via {@link IsSiteAdmin}.
 */
@RestController
@RequestMapping("/api/admin/settings")
@CrossOrigin(origins = "*", maxAge = 3600)
public class SiteAdminController {

    private static final Logger log = LoggerFactory.getLogger(SiteAdminController.class);

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

    // ─────────── Secure file download ───────────

    /**
     * Download the GitHub App private key (.pem) file.
     * <p>
     * Security controls:
     * <ul>
     *   <li>Requires ROLE_ADMIN</li>
     *   <li>Only serves the file whose path is stored in VCS_GITHUB / private-key-path</li>
     *   <li>Path is validated: must be absolute, end with .pem, no traversal</li>
     *   <li>File size limited to 16 KB</li>
     *   <li>Disabled in cloud mode (CloudSiteSettingsService throws UnsupportedOperationException)</li>
     * </ul>
     */
    @GetMapping("/download-key")
    @IsSiteAdmin
    public ResponseEntity<Resource> downloadPrivateKey() {
        try {
            Resource resource = settingsProvider.downloadPrivateKeyFile();
            return ResponseEntity.ok()
                    .contentType(MediaType.APPLICATION_OCTET_STREAM)
                    .header(HttpHeaders.CONTENT_DISPOSITION,
                            "attachment; filename=\"github-app-private-key.pem\"")
                    .body(resource);
        } catch (UnsupportedOperationException e) {
            log.warn("Private key download attempted in cloud mode");
            return ResponseEntity.status(403).build();
        } catch (SecurityException e) {
            log.warn("Private key download blocked: {}", e.getMessage());
            return ResponseEntity.badRequest().build();
        } catch (IllegalStateException e) {
            log.warn("Private key download failed: {}", e.getMessage());
            return ResponseEntity.notFound().build();
        } catch (Exception e) {
            log.error("Private key download error", e);
            return ResponseEntity.internalServerError().build();
        }
    }

    // ─────────── Secure file upload ───────────

    /**
     * Upload a GitHub App private key (.pem) file.
     * <p>
     * The file is saved to a secure directory inside the container
     * and the resulting path is returned so the admin can save it
     * in the VCS_GITHUB settings group.
     */
    @PostMapping(value = "/upload-key", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    @IsSiteAdmin
    public ResponseEntity<Map<String, String>> uploadPrivateKey(
            @RequestParam("file") MultipartFile file) {
        try {
            String path = settingsProvider.uploadPrivateKeyFile(file);
            return ResponseEntity.ok(Map.of("path", path));
        } catch (UnsupportedOperationException e) {
            log.warn("Private key upload attempted in cloud mode");
            return ResponseEntity.status(403).build();
        } catch (SecurityException | IllegalArgumentException e) {
            log.warn("Private key upload rejected: {}", e.getMessage());
            return ResponseEntity.badRequest().body(Map.of("error", e.getMessage()));
        } catch (Exception e) {
            log.error("Private key upload error", e);
            return ResponseEntity.internalServerError().build();
        }
    }
}
