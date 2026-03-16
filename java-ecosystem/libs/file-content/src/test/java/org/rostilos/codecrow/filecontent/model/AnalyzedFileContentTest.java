package org.rostilos.codecrow.filecontent.model;

import org.junit.jupiter.api.Test;

import java.time.OffsetDateTime;

import static org.assertj.core.api.Assertions.assertThat;

class AnalyzedFileContentTest {

    @Test
    void defaultConstructor_shouldSetDefaults() {
        AnalyzedFileContent fc = new AnalyzedFileContent();

        assertThat(fc.getId()).isNull();
        assertThat(fc.getContentHash()).isNull();
        assertThat(fc.getContent()).isNull();
        assertThat(fc.getSizeBytes()).isZero();
        assertThat(fc.getLineCount()).isZero();
        assertThat(fc.getCreatedAt()).isNotNull();
    }

    @Test
    void settersAndGetters_shouldRoundTrip() {
        AnalyzedFileContent fc = new AnalyzedFileContent();

        fc.setContentHash("abc123def456");
        assertThat(fc.getContentHash()).isEqualTo("abc123def456");

        fc.setContent("public class Foo {}");
        assertThat(fc.getContent()).isEqualTo("public class Foo {}");

        fc.setSizeBytes(12345L);
        assertThat(fc.getSizeBytes()).isEqualTo(12345L);

        fc.setLineCount(42);
        assertThat(fc.getLineCount()).isEqualTo(42);
    }

    @Test
    void createdAt_shouldBeSetAutomatically() {
        AnalyzedFileContent fc = new AnalyzedFileContent();
        OffsetDateTime before = OffsetDateTime.now().minusSeconds(1);

        assertThat(fc.getCreatedAt()).isAfter(before);
    }
}
