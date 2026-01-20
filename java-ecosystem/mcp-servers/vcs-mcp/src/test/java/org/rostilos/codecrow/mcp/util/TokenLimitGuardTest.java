package org.rostilos.codecrow.mcp.util;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("TokenLimitGuard")
class TokenLimitGuardTest {

    private String originalMaxTokensProperty;

    @BeforeEach
    void setUp() {
        originalMaxTokensProperty = System.getProperty("max.allowed.tokens");
    }

    @AfterEach
    void tearDown() {
        if (originalMaxTokensProperty != null) {
            System.setProperty("max.allowed.tokens", originalMaxTokensProperty);
        } else {
            System.clearProperty("max.allowed.tokens");
        }
    }

    @Nested
    @DisplayName("isExceededMaxAllowedTokens()")
    class IsExceededMaxAllowedTokensTests {

        @Test
        @DisplayName("should return false for empty string")
        void shouldReturnFalseForEmptyString() {
            boolean exceeded = TokenLimitGuard.isExceededMaxAllowedTokens("");

            assertThat(exceeded).isFalse();
        }

        @Test
        @DisplayName("should return false for small text")
        void shouldReturnFalseForSmallText() {
            String smallText = "This is a small piece of text.";

            boolean exceeded = TokenLimitGuard.isExceededMaxAllowedTokens(smallText);

            assertThat(exceeded).isFalse();
        }

        @Test
        @DisplayName("should return false for text under default limit")
        void shouldReturnFalseForTextUnderDefaultLimit() {
            // Create a moderate-sized text (should be well under 200k tokens)
            String moderateText = "Hello world. ".repeat(1000);

            boolean exceeded = TokenLimitGuard.isExceededMaxAllowedTokens(moderateText);

            assertThat(exceeded).isFalse();
        }

        @Test
        @DisplayName("should return true for very large text exceeding default limit")
        void shouldReturnTrueForVeryLargeText() {
            // Create a very large text that will exceed 200k tokens
            // Each word is roughly 1 token, so we need > 200k words
            String largeText = "word ".repeat(250000);

            boolean exceeded = TokenLimitGuard.isExceededMaxAllowedTokens(largeText);

            assertThat(exceeded).isTrue();
        }

        @Test
        @DisplayName("should use custom max tokens from system property")
        void shouldUseCustomMaxTokensFromSystemProperty() {
            // Set a low limit
            System.setProperty("max.allowed.tokens", "10");

            String text = "This is a test with more than ten tokens in the sentence.";

            boolean exceeded = TokenLimitGuard.isExceededMaxAllowedTokens(text);

            assertThat(exceeded).isTrue();
        }

        @Test
        @DisplayName("should use default limit when property is not set")
        void shouldUseDefaultLimitWhenPropertyNotSet() {
            System.clearProperty("max.allowed.tokens");

            String smallText = "Small text";

            boolean exceeded = TokenLimitGuard.isExceededMaxAllowedTokens(smallText);

            // Default limit is 200000, so this should not exceed
            assertThat(exceeded).isFalse();
        }

        @Test
        @DisplayName("should use default limit when property is empty")
        void shouldUseDefaultLimitWhenPropertyIsEmpty() {
            System.setProperty("max.allowed.tokens", "");

            String smallText = "Small text";

            boolean exceeded = TokenLimitGuard.isExceededMaxAllowedTokens(smallText);

            assertThat(exceeded).isFalse();
        }

        @Test
        @DisplayName("should use default limit when property is invalid")
        void shouldUseDefaultLimitWhenPropertyIsInvalid() {
            System.setProperty("max.allowed.tokens", "not-a-number");

            String smallText = "Small text";

            boolean exceeded = TokenLimitGuard.isExceededMaxAllowedTokens(smallText);

            assertThat(exceeded).isFalse();
        }

        @Test
        @DisplayName("should handle unicode characters")
        void shouldHandleUnicodeCharacters() {
            String unicodeText = "Hello 世界 🌍 Привет мир";

            boolean exceeded = TokenLimitGuard.isExceededMaxAllowedTokens(unicodeText);

            assertThat(exceeded).isFalse();
        }

        @Test
        @DisplayName("should handle code-like content")
        void shouldHandleCodeLikeContent() {
            String codeContent = """
                    public class HelloWorld {
                        public static void main(String[] args) {
                            System.out.println("Hello, World!");
                        }
                    }
                    """;

            boolean exceeded = TokenLimitGuard.isExceededMaxAllowedTokens(codeContent);

            assertThat(exceeded).isFalse();
        }
    }
}
