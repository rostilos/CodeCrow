package org.rostilos.codecrow.analysisengine.util;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("TokenEstimator")
class TokenEstimatorTest {

    @Nested
    @DisplayName("estimateTokens()")
    class EstimateTokensTests {
        @Test void returnsZero_forNull() {
            assertThat(TokenEstimator.estimateTokens(null)).isZero();
        }

        @Test void returnsZero_forEmpty() {
            assertThat(TokenEstimator.estimateTokens("")).isZero();
        }

        @Test void returnsPositive_forNonEmptyText() {
            int tokens = TokenEstimator.estimateTokens("Hello, world! This is a test.");
            assertThat(tokens).isGreaterThan(0);
        }

        @Test void returnsReasonableEstimate_forCode() {
            String code = """
                    public class Main {
                        public static void main(String[] args) {
                            System.out.println("Hello World");
                        }
                    }
                    """;
            int tokens = TokenEstimator.estimateTokens(code);
            assertThat(tokens).isBetween(10, 100);
        }
    }

    @Nested
    @DisplayName("exceedsLimit()")
    class ExceedsLimitTests {
        @Test void returnsFalse_whenUnderLimit() {
            assertThat(TokenEstimator.exceedsLimit("short", 1000)).isFalse();
        }

        @Test void returnsTrue_whenOverLimit() {
            String longText = "word ".repeat(10000);
            assertThat(TokenEstimator.exceedsLimit(longText, 1)).isTrue();
        }

        @Test void returnsFalse_forNull() {
            assertThat(TokenEstimator.exceedsLimit(null, 100)).isFalse();
        }
    }

    @Nested
    @DisplayName("estimateAndCheck()")
    class EstimateAndCheckTests {
        @Test void withinLimit() {
            TokenEstimator.TokenEstimationResult result = TokenEstimator.estimateAndCheck("hello", 1000);
            assertThat(result.exceedsLimit()).isFalse();
            assertThat(result.estimatedTokens()).isGreaterThan(0);
            assertThat(result.maxAllowedTokens()).isEqualTo(1000);
            assertThat(result.utilizationPercentage()).isGreaterThan(0);
            assertThat(result.utilizationPercentage()).isLessThan(100);
        }

        @Test void exceedsLimit() {
            String longText = "word ".repeat(5000);
            TokenEstimator.TokenEstimationResult result = TokenEstimator.estimateAndCheck(longText, 10);
            assertThat(result.exceedsLimit()).isTrue();
            assertThat(result.utilizationPercentage()).isGreaterThan(100);
        }

        @Test void zeroMaxTokens() {
            TokenEstimator.TokenEstimationResult result = TokenEstimator.estimateAndCheck("hello", 0);
            assertThat(result.utilizationPercentage()).isEqualTo(0);
        }

        @Test void toLogString_withinLimit() {
            TokenEstimator.TokenEstimationResult result = TokenEstimator.estimateAndCheck("hi", 1000);
            assertThat(result.toLogString()).contains("within limit");
        }

        @Test void toLogString_exceedsLimit() {
            String longText = "word ".repeat(5000);
            TokenEstimator.TokenEstimationResult result = TokenEstimator.estimateAndCheck(longText, 5);
            assertThat(result.toLogString()).contains("EXCEEDS LIMIT");
        }
    }
}
