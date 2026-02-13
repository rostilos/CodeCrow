package org.rostilos.codecrow.core.model.admin;

/**
 * Groups of site-wide configuration settings.
 * Each group corresponds to one panel / card in the Site Admin UI.
 */
public enum ESiteSettingsGroup {
    VCS_BITBUCKET,
    VCS_BITBUCKET_CONNECT,
    VCS_GITHUB,
    VCS_GITLAB,
    LLM_SYNC,
    EMBEDDING,
    SMTP,
    GOOGLE_OAUTH,
    BASE_URLS
}
