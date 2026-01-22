package org.rostilos.codecrow.vcsclient.bitbucket.model.report;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("CodeInsightsAnnotation")
class CodeInsightsAnnotationTest {

    @Test
    @DisplayName("should create annotation with all fields")
    void shouldCreateAnnotationWithAllFields() {
        CodeInsightsAnnotation annotation = new CodeInsightsAnnotation(
                42,
                "Potential null pointer",
                "src/main/java/Service.java",
                "HIGH"
        );
        
        assertThat(annotation.getLine()).isEqualTo(42);
        assertThat(annotation.getMessage()).isEqualTo("Potential null pointer");
        assertThat(annotation.getPath()).isEqualTo("src/main/java/Service.java");
        assertThat(annotation.getSeverity()).isEqualTo("HIGH");
    }

    @Test
    @DisplayName("should handle null message")
    void shouldHandleNullMessage() {
        CodeInsightsAnnotation annotation = new CodeInsightsAnnotation(
                1,
                null,
                "path.java",
                "LOW"
        );
        
        assertThat(annotation.getMessage()).isNull();
    }

    @Test
    @DisplayName("should handle line number zero")
    void shouldHandleLineNumberZero() {
        CodeInsightsAnnotation annotation = new CodeInsightsAnnotation(
                0,
                "File-level issue",
                "file.java",
                "INFO"
        );
        
        assertThat(annotation.getLine()).isZero();
    }

    @Test
    @DisplayName("should handle various severity levels")
    void shouldHandleVariousSeverityLevels() {
        CodeInsightsAnnotation high = new CodeInsightsAnnotation(1, "msg", "path", "HIGH");
        CodeInsightsAnnotation medium = new CodeInsightsAnnotation(2, "msg", "path", "MEDIUM");
        CodeInsightsAnnotation low = new CodeInsightsAnnotation(3, "msg", "path", "LOW");
        
        assertThat(high.getSeverity()).isEqualTo("HIGH");
        assertThat(medium.getSeverity()).isEqualTo("MEDIUM");
        assertThat(low.getSeverity()).isEqualTo("LOW");
    }
}
