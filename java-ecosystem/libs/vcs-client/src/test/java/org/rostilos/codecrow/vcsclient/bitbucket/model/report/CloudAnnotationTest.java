package org.rostilos.codecrow.vcsclient.bitbucket.model.report;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("CloudAnnotation")
class CloudAnnotationTest {

    @Test
    @DisplayName("should create cloud annotation with all fields")
    void shouldCreateCloudAnnotationWithAllFields() {
        CloudAnnotation annotation = new CloudAnnotation(
                "ext-123",
                42,
                "https://example.com/issue",
                "SQL injection vulnerability",
                "src/main/java/UserService.java",
                "HIGH",
                "BUG"
        );
        
        assertThat(annotation.getExternalId()).isEqualTo("ext-123");
        assertThat(annotation.getLine()).isEqualTo(42);
        assertThat(annotation.getLink()).isEqualTo("https://example.com/issue");
        assertThat(annotation.getMessage()).isEqualTo("SQL injection vulnerability");
        assertThat(annotation.getPath()).isEqualTo("src/main/java/UserService.java");
        assertThat(annotation.getSeverity()).isEqualTo("HIGH");
        assertThat(annotation.getAnnotationType()).isEqualTo("BUG");
    }

    @Test
    @DisplayName("should extend CodeInsightsAnnotation")
    void shouldExtendCodeInsightsAnnotation() {
        CloudAnnotation annotation = new CloudAnnotation(
                "id", 1, "link", "message", "path", "LOW", "VULNERABILITY"
        );
        
        assertThat(annotation).isInstanceOf(CodeInsightsAnnotation.class);
    }

    @Test
    @DisplayName("should handle null external id")
    void shouldHandleNullExternalId() {
        CloudAnnotation annotation = new CloudAnnotation(
                null, 1, "link", "message", "path", "MEDIUM", "CODE_SMELL"
        );
        
        assertThat(annotation.getExternalId()).isNull();
    }

    @Test
    @DisplayName("should handle null link")
    void shouldHandleNullLink() {
        CloudAnnotation annotation = new CloudAnnotation(
                "id", 1, null, "message", "path", "LOW", "CODE_SMELL"
        );
        
        assertThat(annotation.getLink()).isNull();
    }

    @Test
    @DisplayName("should support various annotation types")
    void shouldSupportVariousAnnotationTypes() {
        CloudAnnotation bug = new CloudAnnotation("1", 1, "link", "msg", "path", "HIGH", "BUG");
        CloudAnnotation vulnerability = new CloudAnnotation("2", 2, "link", "msg", "path", "HIGH", "VULNERABILITY");
        CloudAnnotation codeSmell = new CloudAnnotation("3", 3, "link", "msg", "path", "LOW", "CODE_SMELL");
        
        assertThat(bug.getAnnotationType()).isEqualTo("BUG");
        assertThat(vulnerability.getAnnotationType()).isEqualTo("VULNERABILITY");
        assertThat(codeSmell.getAnnotationType()).isEqualTo("CODE_SMELL");
    }
}
