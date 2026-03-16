package org.rostilos.codecrow.filecontent.model;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.pullrequest.PullRequest;

import static org.assertj.core.api.Assertions.assertThat;

class AnalyzedFileSnapshotTest {

    @Test
    void defaultConstructor_shouldSetDefaults() {
        AnalyzedFileSnapshot snap = new AnalyzedFileSnapshot();

        assertThat(snap.getId()).isNull();
        assertThat(snap.getAnalysis()).isNull();
        assertThat(snap.getPullRequest()).isNull();
        assertThat(snap.getBranch()).isNull();
        assertThat(snap.getFilePath()).isNull();
        assertThat(snap.getFileContent()).isNull();
        assertThat(snap.getCommitHash()).isNull();
        assertThat(snap.getCreatedAt()).isNotNull();
    }

    @Test
    void settersAndGetters_shouldRoundTrip() {
        AnalyzedFileSnapshot snap = new AnalyzedFileSnapshot();

        CodeAnalysis analysis = new CodeAnalysis();
        snap.setAnalysis(analysis);
        assertThat(snap.getAnalysis()).isSameAs(analysis);

        PullRequest pr = new PullRequest();
        snap.setPullRequest(pr);
        assertThat(snap.getPullRequest()).isSameAs(pr);

        Branch branch = new Branch();
        snap.setBranch(branch);
        assertThat(snap.getBranch()).isSameAs(branch);

        snap.setFilePath("src/main/java/Foo.java");
        assertThat(snap.getFilePath()).isEqualTo("src/main/java/Foo.java");

        AnalyzedFileContent content = new AnalyzedFileContent();
        snap.setFileContent(content);
        assertThat(snap.getFileContent()).isSameAs(content);

        snap.setCommitHash("abc123def");
        assertThat(snap.getCommitHash()).isEqualTo("abc123def");
    }
}
