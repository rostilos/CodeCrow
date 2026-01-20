package org.rostilos.codecrow.vcsclient.model;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("VcsWebhook")
class VcsWebhookTest {

    @Test
    @DisplayName("should create with all fields")
    void shouldCreateWithAllFields() {
        List<String> events = List.of("push", "pull_request");
        VcsWebhook webhook = new VcsWebhook(
                "webhook-123",
                "https://codecrow.io/webhook",
                true,
                events,
                "CodeCrow Webhook"
        );
        
        assertThat(webhook.id()).isEqualTo("webhook-123");
        assertThat(webhook.url()).isEqualTo("https://codecrow.io/webhook");
        assertThat(webhook.active()).isTrue();
        assertThat(webhook.events()).containsExactly("push", "pull_request");
        assertThat(webhook.description()).isEqualTo("CodeCrow Webhook");
    }

    @Test
    @DisplayName("should create with null values")
    void shouldCreateWithNullValues() {
        VcsWebhook webhook = new VcsWebhook(null, null, false, null, null);
        
        assertThat(webhook.id()).isNull();
        assertThat(webhook.url()).isNull();
        assertThat(webhook.active()).isFalse();
        assertThat(webhook.events()).isNull();
        assertThat(webhook.description()).isNull();
    }

    @Nested
    @DisplayName("matchesUrl")
    class MatchesUrl {

        @Test
        @DisplayName("should return true for exact match")
        void shouldReturnTrueForExactMatch() {
            VcsWebhook webhook = new VcsWebhook(
                    "id", "https://codecrow.io/webhook", true, null, null
            );
            
            assertThat(webhook.matchesUrl("https://codecrow.io/webhook")).isTrue();
        }

        @Test
        @DisplayName("should return true for case-insensitive match")
        void shouldReturnTrueForCaseInsensitiveMatch() {
            VcsWebhook webhook = new VcsWebhook(
                    "id", "HTTPS://CODECROW.IO/WEBHOOK", true, null, null
            );
            
            assertThat(webhook.matchesUrl("https://codecrow.io/webhook")).isTrue();
        }

        @Test
        @DisplayName("should return false for different url")
        void shouldReturnFalseForDifferentUrl() {
            VcsWebhook webhook = new VcsWebhook(
                    "id", "https://codecrow.io/webhook1", true, null, null
            );
            
            assertThat(webhook.matchesUrl("https://codecrow.io/webhook2")).isFalse();
        }

        @Test
        @DisplayName("should return false for null webhook url")
        void shouldReturnFalseForNullWebhookUrl() {
            VcsWebhook webhook = new VcsWebhook("id", null, true, null, null);
            
            assertThat(webhook.matchesUrl("https://codecrow.io/webhook")).isFalse();
        }

        @Test
        @DisplayName("should return false for null target url")
        void shouldReturnFalseForNullTargetUrl() {
            VcsWebhook webhook = new VcsWebhook(
                    "id", "https://codecrow.io/webhook", true, null, null
            );
            
            assertThat(webhook.matchesUrl(null)).isFalse();
        }
    }
}
