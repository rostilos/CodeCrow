package org.rostilos.codecrow.core.model.project.config;

import org.junit.jupiter.api.Test;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class CommentCommandsConfigTest {

    @Test
    void testDefaultConstructor() {
        CommentCommandsConfig config = new CommentCommandsConfig();
        assertThat(config.enabled()).isTrue();
        assertThat(config.rateLimit()).isEqualTo(CommentCommandsConfig.DEFAULT_RATE_LIMIT);
        assertThat(config.rateLimitWindowMinutes()).isEqualTo(CommentCommandsConfig.DEFAULT_RATE_LIMIT_WINDOW_MINUTES);
        assertThat(config.allowPublicRepoCommands()).isFalse();
        assertThat(config.allowedCommands()).isNull();
        assertThat(config.authorizationMode()).isEqualTo(CommentCommandsConfig.DEFAULT_AUTHORIZATION_MODE);
        assertThat(config.allowPrAuthor()).isTrue();
    }

    @Test
    void testConstructorWithEnabledOnly() {
        CommentCommandsConfig config = new CommentCommandsConfig(false);
        assertThat(config.enabled()).isFalse();
        assertThat(config.rateLimit()).isEqualTo(CommentCommandsConfig.DEFAULT_RATE_LIMIT);
        assertThat(config.rateLimitWindowMinutes()).isEqualTo(CommentCommandsConfig.DEFAULT_RATE_LIMIT_WINDOW_MINUTES);
    }

    @Test
    void testFullConstructor() {
        List<String> allowedCommands = Arrays.asList("analyze", "summarize");
        CommentCommandsConfig config = new CommentCommandsConfig(
            true, 20, 120, true, allowedCommands, CommandAuthorizationMode.ALLOWED_USERS_ONLY, false
        );
        assertThat(config.enabled()).isTrue();
        assertThat(config.rateLimit()).isEqualTo(20);
        assertThat(config.rateLimitWindowMinutes()).isEqualTo(120);
        assertThat(config.allowPublicRepoCommands()).isTrue();
        assertThat(config.allowedCommands()).isEqualTo(allowedCommands);
        assertThat(config.authorizationMode()).isEqualTo(CommandAuthorizationMode.ALLOWED_USERS_ONLY);
        assertThat(config.allowPrAuthor()).isFalse();
    }

    @Test
    void testGetEffectiveRateLimit_WithValue() {
        CommentCommandsConfig config = new CommentCommandsConfig(
            true, 15, 60, false, null, CommandAuthorizationMode.ANYONE, true
        );
        assertThat(config.getEffectiveRateLimit()).isEqualTo(15);
    }

    @Test
    void testGetEffectiveRateLimit_WithNull() {
        CommentCommandsConfig config = new CommentCommandsConfig(
            true, null, 60, false, null, CommandAuthorizationMode.ANYONE, true
        );
        assertThat(config.getEffectiveRateLimit()).isEqualTo(CommentCommandsConfig.DEFAULT_RATE_LIMIT);
    }

    @Test
    void testGetEffectiveRateLimitWindowMinutes_WithValue() {
        CommentCommandsConfig config = new CommentCommandsConfig(
            true, 10, 90, false, null, CommandAuthorizationMode.ANYONE, true
        );
        assertThat(config.getEffectiveRateLimitWindowMinutes()).isEqualTo(90);
    }

    @Test
    void testGetEffectiveRateLimitWindowMinutes_WithNull() {
        CommentCommandsConfig config = new CommentCommandsConfig(
            true, 10, null, false, null, CommandAuthorizationMode.ANYONE, true
        );
        assertThat(config.getEffectiveRateLimitWindowMinutes())
            .isEqualTo(CommentCommandsConfig.DEFAULT_RATE_LIMIT_WINDOW_MINUTES);
    }

    @Test
    void testIsCommandAllowed_NullList() {
        CommentCommandsConfig config = new CommentCommandsConfig(
            true, 10, 60, false, null, CommandAuthorizationMode.ANYONE, true
        );
        assertThat(config.isCommandAllowed("analyze")).isTrue();
        assertThat(config.isCommandAllowed("summarize")).isTrue();
        assertThat(config.isCommandAllowed("ask")).isTrue();
    }

    @Test
    void testIsCommandAllowed_EmptyList() {
        CommentCommandsConfig config = new CommentCommandsConfig(
            true, 10, 60, false, Collections.emptyList(), CommandAuthorizationMode.ANYONE, true
        );
        assertThat(config.isCommandAllowed("analyze")).isTrue();
    }

    @Test
    void testIsCommandAllowed_WithAllowedCommands() {
        List<String> allowedCommands = Arrays.asList("analyze", "summarize");
        CommentCommandsConfig config = new CommentCommandsConfig(
            true, 10, 60, false, allowedCommands, CommandAuthorizationMode.ANYONE, true
        );
        assertThat(config.isCommandAllowed("analyze")).isTrue();
        assertThat(config.isCommandAllowed("summarize")).isTrue();
        assertThat(config.isCommandAllowed("ask")).isFalse();
    }

    @Test
    void testAllowsPublicRepoCommands_True() {
        CommentCommandsConfig config = new CommentCommandsConfig(
            true, 10, 60, true, null, CommandAuthorizationMode.ANYONE, true
        );
        assertThat(config.allowsPublicRepoCommands()).isTrue();
    }

    @Test
    void testAllowsPublicRepoCommands_False() {
        CommentCommandsConfig config = new CommentCommandsConfig(
            true, 10, 60, false, null, CommandAuthorizationMode.ANYONE, true
        );
        assertThat(config.allowsPublicRepoCommands()).isFalse();
    }

    @Test
    void testAllowsPublicRepoCommands_Null() {
        CommentCommandsConfig config = new CommentCommandsConfig(
            true, 10, 60, null, null, CommandAuthorizationMode.ANYONE, true
        );
        assertThat(config.allowsPublicRepoCommands()).isFalse();
    }

    @Test
    void testGetEffectiveAuthorizationMode_WithValue() {
        CommentCommandsConfig config = new CommentCommandsConfig(
            true, 10, 60, false, null, CommandAuthorizationMode.PR_AUTHOR_ONLY, true
        );
        assertThat(config.getEffectiveAuthorizationMode()).isEqualTo(CommandAuthorizationMode.PR_AUTHOR_ONLY);
    }

    @Test
    void testGetEffectiveAuthorizationMode_WithNull() {
        CommentCommandsConfig config = new CommentCommandsConfig(
            true, 10, 60, false, null, null, true
        );
        assertThat(config.getEffectiveAuthorizationMode())
            .isEqualTo(CommentCommandsConfig.DEFAULT_AUTHORIZATION_MODE);
    }

    @Test
    void testIsPrAuthorAllowed_True() {
        CommentCommandsConfig config = new CommentCommandsConfig(
            true, 10, 60, false, null, CommandAuthorizationMode.ANYONE, true
        );
        assertThat(config.isPrAuthorAllowed()).isTrue();
    }

    @Test
    void testIsPrAuthorAllowed_False() {
        CommentCommandsConfig config = new CommentCommandsConfig(
            true, 10, 60, false, null, CommandAuthorizationMode.ANYONE, false
        );
        assertThat(config.isPrAuthorAllowed()).isFalse();
    }

    @Test
    void testIsPrAuthorAllowed_Null() {
        CommentCommandsConfig config = new CommentCommandsConfig(
            true, 10, 60, false, null, CommandAuthorizationMode.ANYONE, null
        );
        assertThat(config.isPrAuthorAllowed()).isTrue();
    }

    @Test
    void testConstants() {
        assertThat(CommentCommandsConfig.DEFAULT_RATE_LIMIT).isEqualTo(10);
        assertThat(CommentCommandsConfig.DEFAULT_RATE_LIMIT_WINDOW_MINUTES).isEqualTo(60);
        assertThat(CommentCommandsConfig.DEFAULT_AUTHORIZATION_MODE).isEqualTo(CommandAuthorizationMode.ANYONE);
    }

    @Test
    void testRecordEquality() {
        CommentCommandsConfig config1 = new CommentCommandsConfig(
            true, 10, 60, false, null, CommandAuthorizationMode.ANYONE, true
        );
        CommentCommandsConfig config2 = new CommentCommandsConfig(
            true, 10, 60, false, null, CommandAuthorizationMode.ANYONE, true
        );
        assertThat(config1).isEqualTo(config2);
        assertThat(config1.hashCode()).isEqualTo(config2.hashCode());
    }
}
