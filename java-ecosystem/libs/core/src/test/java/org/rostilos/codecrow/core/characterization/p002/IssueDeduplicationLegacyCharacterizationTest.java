package org.rostilos.codecrow.core.characterization.p002;

import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.codeanalysis.IssueCategory;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.service.IssueDeduplicationService;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

@Tag("legacy-defect")
class IssueDeduplicationLegacyCharacterizationTest {

    private final IssueDeduplicationService service = new IssueDeduplicationService();

    @Test
    void legacyDefectSameFileLineAndCategoryDeleteOneDistinctFinding() {
        CodeAnalysisIssue nullFailure = issue(
                "src/App.java", 10, "Null dereference in request parsing", IssueSeverity.MEDIUM);
        CodeAnalysisIssue boundsFailure = issue(
                "src/App.java", 10, "Bounds failure in request parsing", IssueSeverity.HIGH);

        List<CodeAnalysisIssue> result = service.deduplicateAtIngestion(List.of(nullFailure, boundsFailure));

        assertThat(result).singleElement().isSameAs(boundsFailure);
    }

    @Test
    void legacyDefectLineOneActsAsWholeFileWildcard() {
        CodeAnalysisIssue importFailure = issue(
                "src/App.java", 1, "Import-time failure", IssueSeverity.HIGH);
        CodeAnalysisIssue runtimeFailure = issue(
                "src/App.java", 99, "Runtime state corruption", IssueSeverity.MEDIUM);

        List<CodeAnalysisIssue> result = service.deduplicateAtIngestion(List.of(importFailure, runtimeFailure));

        assertThat(result).singleElement().isSameAs(importFailure);
    }

    @Test
    void ordinaryDifferentAnchorsSurvive() {
        CodeAnalysisIssue first = issue("src/App.java", 10, "First", IssueSeverity.HIGH);
        CodeAnalysisIssue second = issue("src/App.java", 20, "Second", IssueSeverity.HIGH);

        assertThat(service.deduplicateAtIngestion(List.of(first, second)))
                .containsExactly(first, second);
    }

    private CodeAnalysisIssue issue(String file, int line, String reason, IssueSeverity severity) {
        CodeAnalysisIssue issue = new CodeAnalysisIssue();
        issue.setFilePath(file);
        issue.setLineNumber(line);
        issue.setIssueCategory(IssueCategory.BUG_RISK);
        issue.setSeverity(severity);
        issue.setTitle(reason);
        issue.setReason(reason);
        issue.setIssueFingerprint("fp:" + file + ":" + line + ":" + reason);
        return issue;
    }
}
