package org.rostilos.codecrow.core.dto.admin;

import org.rostilos.codecrow.core.model.admin.ESiteSettingsGroup;

import java.util.Map;

/**
 * Reports which settings groups have been configured.
 * Used by the first-time setup wizard to show progress.
 */
public record ConfigurationStatusDTO(
        Map<ESiteSettingsGroup, Boolean> groups,
        boolean setupComplete
) {
}
