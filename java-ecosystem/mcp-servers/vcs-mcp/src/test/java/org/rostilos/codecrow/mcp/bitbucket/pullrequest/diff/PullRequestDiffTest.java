package org.rostilos.codecrow.mcp.bitbucket.pullrequest.diff;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("PullRequestDiff")
class PullRequestDiffTest {

    @Test
    @DisplayName("should set and get files")
    void shouldSetAndGetFiles() {
        PullRequestDiff diff = new PullRequestDiff();
        
        FileDiff file1 = new FileDiff();
        file1.setFilePath("file1.java");
        
        FileDiff file2 = new FileDiff();
        file2.setFilePath("file2.java");
        
        List<FileDiff> files = List.of(file1, file2);
        diff.setFiles(files);
        
        assertThat(diff.getFiles()).hasSize(2);
        assertThat(diff.getFiles().get(0).getFilePath()).isEqualTo("file1.java");
        assertThat(diff.getFiles().get(1).getFilePath()).isEqualTo("file2.java");
    }

    @Test
    @DisplayName("should handle empty file list")
    void shouldHandleEmptyFileList() {
        PullRequestDiff diff = new PullRequestDiff();
        diff.setFiles(Collections.emptyList());
        
        assertThat(diff.getFiles()).isEmpty();
    }

    @Test
    @DisplayName("should handle null file list")
    void shouldHandleNullFileList() {
        PullRequestDiff diff = new PullRequestDiff();
        
        assertThat(diff.getFiles()).isNull();
    }

    @Test
    @DisplayName("should handle mutable file list")
    void shouldHandleMutableFileList() {
        PullRequestDiff diff = new PullRequestDiff();
        List<FileDiff> files = new ArrayList<>();
        
        FileDiff file1 = new FileDiff();
        file1.setFilePath("initial.java");
        files.add(file1);
        
        diff.setFiles(files);
        
        assertThat(diff.getFiles()).hasSize(1);
    }
}
