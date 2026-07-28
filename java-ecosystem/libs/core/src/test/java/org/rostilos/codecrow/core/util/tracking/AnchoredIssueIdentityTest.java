package org.rostilos.codecrow.core.util.tracking;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.branch.BranchIssue;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.codeanalysis.IssueCategory;
import org.rostilos.codecrow.core.model.codeanalysis.IssueScope;

import static org.assertj.core.api.Assertions.assertThat;

class AnchoredIssueIdentityTest {

    private static final String LINE_HASH = "0123456789abcdef0123456789abcdef";

    @Test
    void branchStorageIdentityIncludesExactRepositoryPath() {
        CodeAnalysisIssue first = issue("src/first/Config.php", "Unsafe fallback", LINE_HASH);
        CodeAnalysisIssue second = issue("src/second/Config.php", "Unsafe fallback", LINE_HASH);

        assertThat(AnchoredIssueIdentity.forBranchStorage(first))
                .isNotEqualTo(AnchoredIssueIdentity.forBranchStorage(second));
    }

    @Test
    void equivalentPathSeparatorsAndPrefixesHaveTheSameIdentity() {
        CodeAnalysisIssue first = issue("./src/module/App.java", "Unchecked result", LINE_HASH);
        CodeAnalysisIssue second = issue("\\src\\module\\App.java", "Unchecked result", LINE_HASH);

        assertThat(AnchoredIssueIdentity.forBranchStorage(first))
                .isEqualTo(AnchoredIssueIdentity.forBranchStorage(second));
    }

    @Test
    void categoryDriftDoesNotChangeAnOtherwiseExactIdentity() {
        CodeAnalysisIssue first = issue("src/App.java", "Unchecked result", LINE_HASH);
        first.setIssueCategory(IssueCategory.BUG_RISK);
        CodeAnalysisIssue second = issue("src/App.java", "Unchecked result", LINE_HASH);
        second.setIssueCategory(IssueCategory.CODE_QUALITY);

        assertThat(AnchoredIssueIdentity.forBranchStorage(first))
                .isEqualTo(AnchoredIssueIdentity.forBranchStorage(second));
    }

    @Test
    void unanchoredPlaceholderFingerprintIsNotMergeable() {
        CodeAnalysisIssue issue = issue("src/App.java", "File contract", null);
        issue.setIssueScope(IssueScope.FILE);
        issue.setLineNumber(1);
        issue.setCodeSnippet(null);

        assertThat(issue.getContentFingerprint()).isNotBlank();
        assertThat(AnchoredIssueIdentity.forFingerprint(
                issue,
                issue.getContentFingerprint()
        )).isNull();
        assertThat(AnchoredIssueIdentity.forBranchStorage(issue)).isNull();
    }

    @Test
    void exactSnippetCanAnchorAFileScopedFinding() {
        CodeAnalysisIssue first = issue("src/App.java", "File contract", null);
        first.setIssueScope(IssueScope.FILE);
        first.setLineNumber(1);
        first.setCodeSnippet("return unsafe();\r\n");
        CodeAnalysisIssue second = issue("src/App.java", "File contract", null);
        second.setIssueScope(IssueScope.FILE);
        second.setLineNumber(1);
        second.setCodeSnippet("return unsafe();\n");

        assertThat(AnchoredIssueIdentity.forBranchStorage(first))
                .isNotNull()
                .isEqualTo(AnchoredIssueIdentity.forBranchStorage(second));
    }

    @Test
    void branchIssueIdentityRecomputesFromFieldsInsteadOfHashingStoredValueAgain() {
        CodeAnalysisIssue source = issue(
                "src/App.java",
                "Unchecked result",
                LINE_HASH
        );
        BranchIssue branchIssue = BranchIssue.fromCodeAnalysisIssue(source, new Branch());

        assertThat(branchIssue.getContentFingerprint())
                .isEqualTo(AnchoredIssueIdentity.forBranchStorage(source));
        assertThat(AnchoredIssueIdentity.forBranchStorage(branchIssue))
                .isEqualTo(branchIssue.getContentFingerprint());
    }

    private static CodeAnalysisIssue issue(
            String path,
            String title,
            String lineHash
    ) {
        CodeAnalysisIssue issue = new CodeAnalysisIssue();
        issue.setFilePath(path);
        issue.setLineNumber(12);
        issue.setIssueScope(IssueScope.LINE);
        issue.setIssueCategory(IssueCategory.BUG_RISK);
        issue.setTitle(title);
        issue.setReason("Exact defect evidence");
        issue.setLineHash(lineHash);
        issue.setCodeSnippet("dangerousCall();");
        issue.setIssueFingerprint(IssueFingerprint.compute(
                issue.getIssueCategory(), lineHash, title));
        issue.setContentFingerprint(
                IssueFingerprint.computeContentFingerprint(lineHash, title));
        return issue;
    }
}
