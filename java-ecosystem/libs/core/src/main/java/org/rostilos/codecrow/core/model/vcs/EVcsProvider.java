package org.rostilos.codecrow.core.model.vcs;

import java.util.Locale;

public enum EVcsProvider {
    BITBUCKET_CLOUD, GITHUB;

    private EVcsProvider() {
    }

    public static EVcsProvider fromId(String gitProviderId) {
        return valueOf(gitProviderId.toUpperCase(Locale.ENGLISH));
    }
}
