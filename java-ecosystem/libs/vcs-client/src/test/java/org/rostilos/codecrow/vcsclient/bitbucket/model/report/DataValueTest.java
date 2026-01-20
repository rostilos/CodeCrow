package org.rostilos.codecrow.vcsclient.bitbucket.model.report;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("DataValue")
class DataValueTest {

    @Nested
    @DisplayName("Link")
    class LinkTests {

        @Test
        @DisplayName("should create Link with linktext and href")
        void shouldCreateLinkWithLinktextAndHref() {
            DataValue.Link link = new DataValue.Link("Click here", "https://example.com");
            
            assertThat(link.linktext()).isEqualTo("Click here");
            assertThat(link.href()).isEqualTo("https://example.com");
        }

        @Test
        @DisplayName("should be instance of DataValue")
        void shouldBeInstanceOfDataValue() {
            DataValue link = new DataValue.Link("text", "url");
            
            assertThat(link).isInstanceOf(DataValue.class);
        }
    }

    @Nested
    @DisplayName("CloudLink")
    class CloudLinkTests {

        @Test
        @DisplayName("should create CloudLink with text and href")
        void shouldCreateCloudLinkWithTextAndHref() {
            DataValue.CloudLink cloudLink = new DataValue.CloudLink("View Report", "https://cloud.example.com");
            
            assertThat(cloudLink.text()).isEqualTo("View Report");
            assertThat(cloudLink.href()).isEqualTo("https://cloud.example.com");
        }

        @Test
        @DisplayName("should be instance of DataValue")
        void shouldBeInstanceOfDataValue() {
            DataValue cloudLink = new DataValue.CloudLink("text", "url");
            
            assertThat(cloudLink).isInstanceOf(DataValue.class);
        }
    }

    @Nested
    @DisplayName("Text")
    class TextTests {

        @Test
        @DisplayName("should create Text with value")
        void shouldCreateTextWithValue() {
            DataValue.Text text = new DataValue.Text("Some text value");
            
            assertThat(text.value()).isEqualTo("Some text value");
        }

        @Test
        @DisplayName("should be instance of DataValue")
        void shouldBeInstanceOfDataValue() {
            DataValue text = new DataValue.Text("value");
            
            assertThat(text).isInstanceOf(DataValue.class);
        }

        @Test
        @DisplayName("should handle numeric string values")
        void shouldHandleNumericStringValues() {
            DataValue.Text text = new DataValue.Text("42");
            
            assertThat(text.value()).isEqualTo("42");
        }

        @Test
        @DisplayName("should handle empty string")
        void shouldHandleEmptyString() {
            DataValue.Text text = new DataValue.Text("");
            
            assertThat(text.value()).isEmpty();
        }

        @Test
        @DisplayName("should handle null value")
        void shouldHandleNullValue() {
            DataValue.Text text = new DataValue.Text(null);
            
            assertThat(text.value()).isNull();
        }
    }
}
