package org.rostilos.codecrow.pipelineagent.generic.service;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("CommandAuthorizationService.AuthorizationResult")
class AuthorizationResultTest {

    @Nested
    @DisplayName("allowed()")
    class AllowedFactory {

        @Test
        @DisplayName("should create allowed result with reason")
        void shouldCreateAllowedResultWithReason() {
            CommandAuthorizationService.AuthorizationResult result = 
                    CommandAuthorizationService.AuthorizationResult.allowed("PR author");
            
            assertThat(result.authorized()).isTrue();
            assertThat(result.reason()).isEqualTo("PR author");
        }
    }

    @Nested
    @DisplayName("denied()")
    class DeniedFactory {

        @Test
        @DisplayName("should create denied result with reason")
        void shouldCreateDeniedResultWithReason() {
            CommandAuthorizationService.AuthorizationResult result = 
                    CommandAuthorizationService.AuthorizationResult.denied("User not in allowed list");
            
            assertThat(result.authorized()).isFalse();
            assertThat(result.reason()).isEqualTo("User not in allowed list");
        }
    }

    @Nested
    @DisplayName("Record accessors")
    class RecordAccessors {

        @Test
        @DisplayName("should return authorized value")
        void shouldReturnAuthorizedValue() {
            CommandAuthorizationService.AuthorizationResult allowed = 
                    new CommandAuthorizationService.AuthorizationResult(true, "reason");
            CommandAuthorizationService.AuthorizationResult denied = 
                    new CommandAuthorizationService.AuthorizationResult(false, "reason");
            
            assertThat(allowed.authorized()).isTrue();
            assertThat(denied.authorized()).isFalse();
        }

        @Test
        @DisplayName("should return reason")
        void shouldReturnReason() {
            CommandAuthorizationService.AuthorizationResult result = 
                    new CommandAuthorizationService.AuthorizationResult(true, "Test reason");
            
            assertThat(result.reason()).isEqualTo("Test reason");
        }
    }
}
