package org.rostilos.codecrow.publicshare.api;

/** A raw public-share credential. The value is returned once and is never persisted. */
public record IssuedPublicShare(String token) {

    public String toFrontendUrl(String frontendBaseUrl) {
        if (frontendBaseUrl == null || frontendBaseUrl.isBlank()) {
            throw new IllegalArgumentException("Frontend base URL is required for a public share link.");
        }
        String base = frontendBaseUrl.trim().replaceAll("/+$", "");
        // Fragments are not sent in HTTP request targets or Referer headers.
        return base + "/share#token=" + token;
    }
}
