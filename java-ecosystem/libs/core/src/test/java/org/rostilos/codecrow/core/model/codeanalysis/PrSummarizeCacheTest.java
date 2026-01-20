package org.rostilos.codecrow.core.model.codeanalysis;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.project.Project;

import java.time.OffsetDateTime;

import static org.assertj.core.api.Assertions.assertThat;

class PrSummarizeCacheTest {

    @Test
    void testDefaultConstructor() {
        PrSummarizeCache cache = new PrSummarizeCache();
        assertThat(cache.getId()).isNull();
        assertThat(cache.getProject()).isNull();
        assertThat(cache.getCommitHash()).isNull();
        assertThat(cache.getPrNumber()).isNull();
        assertThat(cache.getSummaryContent()).isNull();
        assertThat(cache.getDiagramContent()).isNull();
        assertThat(cache.getDiagramType()).isNull();
        assertThat(cache.isRagContextUsed()).isFalse();
        assertThat(cache.getSourceBranchName()).isNull();
        assertThat(cache.getTargetBranchName()).isNull();
    }

    @Test
    void testSetAndGetProject() {
        PrSummarizeCache cache = new PrSummarizeCache();
        Project project = new Project();
        cache.setProject(project);
        assertThat(cache.getProject()).isEqualTo(project);
    }

    @Test
    void testSetAndGetCommitHash() {
        PrSummarizeCache cache = new PrSummarizeCache();
        cache.setCommitHash("abc123def456");
        assertThat(cache.getCommitHash()).isEqualTo("abc123def456");
    }

    @Test
    void testSetAndGetPrNumber() {
        PrSummarizeCache cache = new PrSummarizeCache();
        cache.setPrNumber(42L);
        assertThat(cache.getPrNumber()).isEqualTo(42L);
    }

    @Test
    void testSetAndGetSummaryContent() {
        PrSummarizeCache cache = new PrSummarizeCache();
        String summary = "This PR adds new feature X";
        cache.setSummaryContent(summary);
        assertThat(cache.getSummaryContent()).isEqualTo(summary);
    }

    @Test
    void testSetAndGetDiagramContent() {
        PrSummarizeCache cache = new PrSummarizeCache();
        String diagram = "graph TD; A-->B;";
        cache.setDiagramContent(diagram);
        assertThat(cache.getDiagramContent()).isEqualTo(diagram);
    }

    @Test
    void testSetAndGetDiagramType() {
        PrSummarizeCache cache = new PrSummarizeCache();
        cache.setDiagramType(PrSummarizeCache.DiagramType.MERMAID);
        assertThat(cache.getDiagramType()).isEqualTo(PrSummarizeCache.DiagramType.MERMAID);
    }

    @Test
    void testSetAndGetRagContextUsed() {
        PrSummarizeCache cache = new PrSummarizeCache();
        cache.setRagContextUsed(true);
        assertThat(cache.isRagContextUsed()).isTrue();
    }

    @Test
    void testSetAndGetSourceBranchName() {
        PrSummarizeCache cache = new PrSummarizeCache();
        cache.setSourceBranchName("feature/new-feature");
        assertThat(cache.getSourceBranchName()).isEqualTo("feature/new-feature");
    }

    @Test
    void testSetAndGetTargetBranchName() {
        PrSummarizeCache cache = new PrSummarizeCache();
        cache.setTargetBranchName("main");
        assertThat(cache.getTargetBranchName()).isEqualTo("main");
    }

    @Test
    void testSetAndGetExpiresAt() {
        PrSummarizeCache cache = new PrSummarizeCache();
        OffsetDateTime expiresAt = OffsetDateTime.now().plusDays(7);
        cache.setExpiresAt(expiresAt);
        assertThat(cache.getExpiresAt()).isEqualTo(expiresAt);
    }

    @Test
    void testDiagramTypeEnum() {
        assertThat(PrSummarizeCache.DiagramType.values()).containsExactly(
            PrSummarizeCache.DiagramType.MERMAID,
            PrSummarizeCache.DiagramType.ASCII,
            PrSummarizeCache.DiagramType.NONE
        );
        assertThat(PrSummarizeCache.DiagramType.valueOf("MERMAID"))
            .isEqualTo(PrSummarizeCache.DiagramType.MERMAID);
    }

    @Test
    void testFullPrSummarizeCacheSetup() {
        PrSummarizeCache cache = new PrSummarizeCache();
        Project project = new Project();
        cache.setProject(project);
        cache.setCommitHash("abc123");
        cache.setPrNumber(100L);
        cache.setSummaryContent("Summary");
        cache.setDiagramContent("diagram");
        cache.setDiagramType(PrSummarizeCache.DiagramType.MERMAID);
        cache.setRagContextUsed(true);
        cache.setSourceBranchName("feature");
        cache.setTargetBranchName("main");
        OffsetDateTime expiresAt = OffsetDateTime.now().plusDays(30);
        cache.setExpiresAt(expiresAt);

        assertThat(cache.getProject()).isEqualTo(project);
        assertThat(cache.getCommitHash()).isEqualTo("abc123");
        assertThat(cache.getPrNumber()).isEqualTo(100L);
        assertThat(cache.getSummaryContent()).isEqualTo("Summary");
        assertThat(cache.getDiagramContent()).isEqualTo("diagram");
        assertThat(cache.getDiagramType()).isEqualTo(PrSummarizeCache.DiagramType.MERMAID);
        assertThat(cache.isRagContextUsed()).isTrue();
        assertThat(cache.getSourceBranchName()).isEqualTo("feature");
        assertThat(cache.getTargetBranchName()).isEqualTo("main");
        assertThat(cache.getExpiresAt()).isEqualTo(expiresAt);
    }
}
