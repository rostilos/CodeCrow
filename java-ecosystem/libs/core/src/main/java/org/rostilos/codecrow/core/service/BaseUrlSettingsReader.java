package org.rostilos.codecrow.core.service;

import org.rostilos.codecrow.core.dto.admin.BaseUrlSettingsDTO;
import org.rostilos.codecrow.core.model.admin.ESiteSettingsGroup;
import org.rostilos.codecrow.core.model.admin.SiteSettings;
import org.rostilos.codecrow.core.persistence.repository.admin.SiteSettingsRepository;
import org.springframework.stereotype.Component;

import java.util.Map;
import java.util.stream.Collectors;

/**
 * Reads base URL settings from the {@code site_settings} database table.
 * <p>
 * Lives in {@code core} so that every module (web-server, pipeline-agent,
 * vcs-client, etc.) can resolve public-facing URLs from the admin panel
 * without depending on the web-server's {@code ISiteSettingsProvider}.
 * <p>
 * Base URLs are never encrypted, so no {@code TokenEncryptionService} is needed.
 */
@Component
public class BaseUrlSettingsReader {

    private final SiteSettingsRepository repository;

    public BaseUrlSettingsReader(SiteSettingsRepository repository) {
        this.repository = repository;
    }

    /**
     * Read base URL settings from the database.
     * Falls back to localhost defaults when values are not yet configured.
     */
    public BaseUrlSettingsDTO getBaseUrls() {
        Map<String, String> m = repository.findByConfigGroup(ESiteSettingsGroup.BASE_URLS)
                .stream()
                .collect(Collectors.toMap(
                        SiteSettings::getConfigKey,
                        s -> s.getConfigValue() != null ? s.getConfigValue() : "",
                        (a, b) -> b
                ));

        return new BaseUrlSettingsDTO(
                m.getOrDefault(BaseUrlSettingsDTO.KEY_BASE_URL, "http://localhost:8081"),
                m.getOrDefault(BaseUrlSettingsDTO.KEY_FRONTEND_URL, "http://localhost:8080"),
                m.getOrDefault(BaseUrlSettingsDTO.KEY_WEBHOOK_BASE_URL, "http://localhost:8082")
        );
    }
}
