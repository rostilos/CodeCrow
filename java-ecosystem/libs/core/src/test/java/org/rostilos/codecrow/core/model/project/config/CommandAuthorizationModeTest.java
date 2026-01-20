package org.rostilos.codecrow.core.model.project.config;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("CommandAuthorizationMode")
class CommandAuthorizationModeTest {

    @Test
    @DisplayName("should have all expected values")
    void shouldHaveAllExpectedValues() {
        CommandAuthorizationMode[] values = CommandAuthorizationMode.values();
        
        assertThat(values).hasSize(3);
        assertThat(values).contains(
                CommandAuthorizationMode.ANYONE,
                CommandAuthorizationMode.ALLOWED_USERS_ONLY,
                CommandAuthorizationMode.PR_AUTHOR_ONLY
        );
    }

    @Test
    @DisplayName("valueOf should return correct enum")
    void valueOfShouldReturnCorrectEnum() {
        assertThat(CommandAuthorizationMode.valueOf("ANYONE")).isEqualTo(CommandAuthorizationMode.ANYONE);
        assertThat(CommandAuthorizationMode.valueOf("ALLOWED_USERS_ONLY")).isEqualTo(CommandAuthorizationMode.ALLOWED_USERS_ONLY);
        assertThat(CommandAuthorizationMode.valueOf("PR_AUTHOR_ONLY")).isEqualTo(CommandAuthorizationMode.PR_AUTHOR_ONLY);
    }

    @Test
    @DisplayName("name should return string representation")
    void nameShouldReturnStringRepresentation() {
        assertThat(CommandAuthorizationMode.ANYONE.name()).isEqualTo("ANYONE");
        assertThat(CommandAuthorizationMode.ALLOWED_USERS_ONLY.name()).isEqualTo("ALLOWED_USERS_ONLY");
    }
}
