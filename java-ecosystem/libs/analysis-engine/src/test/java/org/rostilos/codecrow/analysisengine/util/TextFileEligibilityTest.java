package org.rostilos.codecrow.analysisengine.util;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class TextFileEligibilityTest {

    @Test
    void excludesKnownBinaryAssetsCaseInsensitively() {
        assertThat(TextFileEligibility.isTextCandidate("images/Box.jpg")).isFalse();
        assertThat(TextFileEligibility.isTextCandidate("images/Label.JPEG")).isFalse();
        assertThat(TextFileEligibility.isTextCandidate("fonts/storefront.woff2")).isFalse();
        assertThat(TextFileEligibility.isTextCandidate("artifacts/module.jar")).isFalse();
    }

    @Test
    void retainsFrameworkAndSourceTextWithoutAnAllowlist() {
        assertThat(TextFileEligibility.isTextCandidate("app/etc/di.xml")).isTrue();
        assertThat(TextFileEligibility.isTextCandidate("templates/item.phtml")).isTrue();
        assertThat(TextFileEligibility.isTextCandidate("web/images/logo.svg")).isTrue();
        assertThat(TextFileEligibility.isTextCandidate(".htaccess")).isTrue();
    }

    @Test
    void classifiesProviderContentUsingBinaryAndSizeBounds() {
        assertThat(TextFileEligibility.isBoundedTextContent("plain text")).isTrue();
        assertThat(TextFileEligibility.isBoundedTextContent("binary\0payload")).isFalse();
        assertThat(TextFileEligibility.isBoundedTextContent(
                "x".repeat((int) TextFileEligibility.MAX_TEXT_CONTENT_BYTES + 1)))
                .isFalse();
    }
}
