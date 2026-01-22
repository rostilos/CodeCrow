package org.rostilos.codecrow.vcsclient.bitbucket.model.report;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("ReportData")
class ReportDataTest {

    @Nested
    @DisplayName("Constructor")
    class ConstructorTests {

        @Test
        @DisplayName("should create with title and Text value")
        void shouldCreateWithTitleAndTextValue() {
            DataValue.Text value = new DataValue.Text("5 issues found");
            ReportData reportData = new ReportData("Issues", value);
            
            assertThat(reportData.getTitle()).isEqualTo("Issues");
            assertThat(reportData.getValue()).isEqualTo(value);
        }

        @Test
        @DisplayName("should set type to TEXT for Text value")
        void shouldSetTypeToTextForTextValue() {
            ReportData reportData = new ReportData("Title", new DataValue.Text("value"));
            
            assertThat(reportData.getType()).isEqualTo("TEXT");
        }

        @Test
        @DisplayName("should set type to LINK for Link value")
        void shouldSetTypeToLinkForLinkValue() {
            ReportData reportData = new ReportData("Link", new DataValue.Link("click", "https://example.com"));
            
            assertThat(reportData.getType()).isEqualTo("LINK");
        }

        @Test
        @DisplayName("should set type to LINK for CloudLink value")
        void shouldSetTypeToLinkForCloudLinkValue() {
            ReportData reportData = new ReportData("Link", new DataValue.CloudLink("click", "https://example.com"));
            
            assertThat(reportData.getType()).isEqualTo("LINK");
        }
    }

    @Nested
    @DisplayName("Getters")
    class GetterTests {

        @Test
        @DisplayName("getTitle() should return the title")
        void getTitleShouldReturnTitle() {
            ReportData reportData = new ReportData("Analysis Result", new DataValue.Text("Passed"));
            
            assertThat(reportData.getTitle()).isEqualTo("Analysis Result");
        }

        @Test
        @DisplayName("getValue() should return the value")
        void getValueShouldReturnValue() {
            DataValue.Text textValue = new DataValue.Text("10");
            ReportData reportData = new ReportData("Count", textValue);
            
            assertThat(reportData.getValue()).isSameAs(textValue);
        }

        @Test
        @DisplayName("getType() should return the type")
        void getTypeShouldReturnType() {
            ReportData textData = new ReportData("Text", new DataValue.Text("value"));
            ReportData linkData = new ReportData("Link", new DataValue.Link("text", "url"));
            
            assertThat(textData.getType()).isEqualTo("TEXT");
            assertThat(linkData.getType()).isEqualTo("LINK");
        }
    }
}
