package org.rostilos.codecrow.scmevidence.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.scmevidence.model.ScmAddedLineEvidence;
import org.rostilos.codecrow.scmevidence.model.ScmCommitEvidence;
import org.rostilos.codecrow.scmevidence.persistence.ScmAddedLineEvidenceRepository;
import org.rostilos.codecrow.scmevidence.persistence.ScmAnalysisReceiptRepository;
import org.rostilos.codecrow.scmevidence.persistence.ScmCommitEvidenceRepository;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.model.VcsCommit;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class ScmEvidenceServiceTest {
    @Mock private ScmCommitEvidenceRepository evidenceRepository;
    @Mock private ScmAnalysisReceiptRepository receiptRepository;
    @Mock private ScmAddedLineEvidenceRepository lineRepository;
    @Mock private VcsClient vcsClient;

    private ScmEvidenceService service;

    @BeforeEach
    void setUp() {
        service = new ScmEvidenceService(
                evidenceRepository, receiptRepository, lineRepository,
                new ScmPromotionPlanner());
    }

    @Test
    void capturesProviderNeutralPatchAuthorAndAddedLineEvidence() throws Exception {
        VcsCommit commit = new VcsCommit(
                "commit-1", "message", "Actual Author", "author@example.test",
                OffsetDateTime.now(), List.of("parent"));
        when(evidenceRepository.findByProjectIdAndCommitHash(42L, "commit-1"))
                .thenReturn(Optional.empty());
        when(vcsClient.getCommitDiff("workspace", "repo", "commit-1"))
                .thenReturn("""
                        diff --git a/src/A.java b/src/A.java
                        --- a/src/A.java
                        +++ b/src/A.java
                        @@ -9,0 +10,1 @@
                        +return secure(value);
                        """);
        when(evidenceRepository.save(any())).thenAnswer(invocation -> invocation.getArgument(0));

        var views = service.capture(
                42L, vcsClient, "workspace", "repo", List.of(commit));

        assertThat(views).singleElement().satisfies(view -> {
            assertThat(view.commitHash()).isEqualTo("commit-1");
            assertThat(view.patchId()).hasSize(64);
            assertThat(view.authorName()).isEqualTo("Actual Author");
            assertThat(view.authorEmail()).isEqualTo("author@example.test");
        });
        var evidence = org.mockito.ArgumentCaptor.forClass(ScmCommitEvidence.class);
        verify(evidenceRepository).save(evidence.capture());
        assertThat(evidence.getValue().getAddedLines()).singleElement()
                .satisfies(line -> {
                    assertThat(line.getFilePath()).isEqualTo("src/A.java");
                    assertThat(line.getNewLineNumber()).isEqualTo(10);
                    assertThat(line.getLineHash())
                            .isEqualTo(PatchIdentity.lineSha256("return secure(value);"));
                });
    }

    @Test
    void issueProvenanceReturnsCommitAuthorNotReviewCommentAuthor() {
        ScmCommitEvidence evidence = new ScmCommitEvidence(
                42L, "introducing-commit", "a".repeat(64),
                "Actual Commit Author", "author@example.test");
        ScmAddedLineEvidence line = new ScmAddedLineEvidence(
                42L, "src/A.java", 10,
                PatchIdentity.lineSha256("return secure(value);"));
        line.setCommitEvidence(evidence);
        when(lineRepository.findMatchingLines(
                42L, List.of("introducing-commit"), "src/A.java",
                PatchIdentity.lineSha256("return secure(value);")))
                .thenReturn(List.of(line));

        var provenance = service.resolveIssueProvenance(
                42L, List.of("introducing-commit"), "src/A.java", 10,
                "return secure(value);");

        assertThat(provenance).isPresent();
        assertThat(provenance.orElseThrow().authorName())
                .isEqualTo("Actual Commit Author");
        assertThat(provenance.orElseThrow().confidence())
                .isEqualTo("EXACT_LINE_AND_CONTENT");
    }

    @Test
    void issueProvenanceResolvesAnAddedLineInsideMultilineSnippet() {
        ScmCommitEvidence evidence = new ScmCommitEvidence(
                42L, "introducing-commit", "a".repeat(64),
                "Actual Commit Author", "author@example.test");
        ScmAddedLineEvidence line = new ScmAddedLineEvidence(
                42L, "src/A.java", 11,
                PatchIdentity.lineSha256("return secure(value);"));
        line.setCommitEvidence(evidence);
        when(lineRepository.findMatchingLines(
                42L, List.of("introducing-commit"), "src/A.java",
                PatchIdentity.lineSha256("if (allowed) {")))
                .thenReturn(List.of());
        when(lineRepository.findMatchingLines(
                42L, List.of("introducing-commit"), "src/A.java",
                PatchIdentity.lineSha256("return secure(value);")))
                .thenReturn(List.of(line));
        when(lineRepository.findMatchingLines(
                42L, List.of("introducing-commit"), "src/A.java",
                PatchIdentity.lineSha256("}")))
                .thenReturn(List.of());

        var provenance = service.resolveIssueProvenance(
                42L, List.of("introducing-commit"), "src/A.java", 10,
                "if (allowed) {\nreturn secure(value);\n}");

        assertThat(provenance).isPresent();
        assertThat(provenance.orElseThrow().lineNumber()).isEqualTo(11);
        assertThat(provenance.orElseThrow().confidence())
                .isEqualTo("EXACT_LINE_AND_CONTENT");
    }

    @Test
    void tenantProjectIdIsMandatoryAtPublicBoundary() {
        assertThat(org.assertj.core.api.Assertions.catchThrowable(() ->
                service.capture(null, vcsClient, "workspace", "repo", List.of())))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("projectId");
        verifyNoInteractions(evidenceRepository, receiptRepository, lineRepository);
    }
}
