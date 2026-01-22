package org.rostilos.codecrow.pipelineagent.generic.service;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("CommentCommandRateLimitService.RateLimitCheckResult")
class RateLimitCheckResultTest {

    @Nested
    @DisplayName("allowed()")
    class AllowedFactory {

        @Test
        @DisplayName("should create allowed result with remaining count")
        void shouldCreateAllowedResultWithRemainingCount() {
            CommentCommandRateLimitService.RateLimitCheckResult result = 
                    CommentCommandRateLimitService.RateLimitCheckResult.allowed(5);
            
            assertThat(result.allowed()).isTrue();
            assertThat(result.remaining()).isEqualTo(5);
            assertThat(result.secondsUntilReset()).isZero();
            assertThat(result.message()).isEqualTo("Command allowed");
        }

        @Test
        @DisplayName("should create allowed result with zero remaining")
        void shouldCreateAllowedResultWithZeroRemaining() {
            CommentCommandRateLimitService.RateLimitCheckResult result = 
                    CommentCommandRateLimitService.RateLimitCheckResult.allowed(0);
            
            assertThat(result.allowed()).isTrue();
            assertThat(result.remaining()).isZero();
        }
    }

    @Nested
    @DisplayName("denied()")
    class DeniedFactory {

        @Test
        @DisplayName("should create denied result with reset time")
        void shouldCreateDeniedResultWithResetTime() {
            CommentCommandRateLimitService.RateLimitCheckResult result = 
                    CommentCommandRateLimitService.RateLimitCheckResult.denied(0, 300L);
            
            assertThat(result.allowed()).isFalse();
            assertThat(result.remaining()).isZero();
            assertThat(result.secondsUntilReset()).isEqualTo(300L);
            assertThat(result.message()).contains("Rate limit exceeded");
            assertThat(result.message()).contains("300 seconds");
        }

        @Test
        @DisplayName("should format message with seconds")
        void shouldFormatMessageWithSeconds() {
            CommentCommandRateLimitService.RateLimitCheckResult result = 
                    CommentCommandRateLimitService.RateLimitCheckResult.denied(0, 60L);
            
            assertThat(result.message()).isEqualTo("Rate limit exceeded. Try again in 60 seconds.");
        }
    }

    @Nested
    @DisplayName("disabled()")
    class DisabledFactory {

        @Test
        @DisplayName("should create disabled result")
        void shouldCreateDisabledResult() {
            CommentCommandRateLimitService.RateLimitCheckResult result = 
                    CommentCommandRateLimitService.RateLimitCheckResult.disabled();
            
            assertThat(result.allowed()).isFalse();
            assertThat(result.remaining()).isZero();
            assertThat(result.secondsUntilReset()).isZero();
            assertThat(result.message()).isEqualTo("Comment commands are not enabled for this project");
        }
    }

    @Nested
    @DisplayName("Record accessors")
    class RecordAccessors {

        @Test
        @DisplayName("should access all fields")
        void shouldAccessAllFields() {
            CommentCommandRateLimitService.RateLimitCheckResult result = 
                    new CommentCommandRateLimitService.RateLimitCheckResult(true, 10, 120L, "Custom message");
            
            assertThat(result.allowed()).isTrue();
            assertThat(result.remaining()).isEqualTo(10);
            assertThat(result.secondsUntilReset()).isEqualTo(120L);
            assertThat(result.message()).isEqualTo("Custom message");
        }
    }
}
