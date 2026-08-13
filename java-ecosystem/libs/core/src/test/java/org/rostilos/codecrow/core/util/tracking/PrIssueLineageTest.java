package org.rostilos.codecrow.core.util.tracking;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.codeanalysis.IssueCategory;
import org.rostilos.codecrow.core.model.codeanalysis.IssueScope;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.model.project.Project;

import java.lang.reflect.Field;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class PrIssueLineageTest {

    @Test
    void projectsAllValidChildrenAsDistinctTips() throws Exception {
        Project project = project(1L);
        CodeAnalysis run1 = analysis(10L, project, 42L, 1);
        CodeAnalysis run2 = analysis(20L, project, 42L, 2);
        CodeAnalysis run3 = analysis(30L, project, 42L, 3);
        CodeAnalysisIssue root = issue(100L, run1);
        CodeAnalysisIssue firstChild = issue(200L, run2);
        CodeAnalysisIssue secondChild = issue(300L, run3);
        firstChild.setTrackedFromIssueId(100L);
        secondChild.setTrackedFromIssueId(100L);

        PrIssueLineage.Projection result = PrIssueLineage.project(
                List.of(root, firstChild, secondChild));

        assertThat(result.activeTips()).containsExactly(firstChild, secondChild);
        assertThat(result.invalidEdges()).isEmpty();
    }

    @Test
    void ignoresForgedCrossProjectAndForwardEdges() throws Exception {
        Project project1 = project(1L);
        Project project2 = project(2L);
        CodeAnalysis run1 = analysis(10L, project1, 42L, 1);
        CodeAnalysis run2OtherProject = analysis(20L, project2, 42L, 2);
        CodeAnalysisIssue root = issue(100L, run1);
        CodeAnalysisIssue forgedChild = issue(200L, run2OtherProject);
        forgedChild.setTrackedFromIssueId(100L);

        PrIssueLineage.Projection result = PrIssueLineage.project(
                List.of(root, forgedChild));

        assertThat(result.activeTips()).containsExactly(root, forgedChild);
        assertThat(result.invalidEdges()).singleElement()
                .extracting(PrIssueLineage.InvalidEdge::reason)
                .isEqualTo("cross-project-or-PR predecessor");
    }

    @Test
    void categoryAndSeverityDoNotChangeLineageReceipt() throws Exception {
        CodeAnalysis analysis = analysis(10L, project(1L), 42L, 1);
        CodeAnalysisIssue first = issue(100L, analysis);
        first.setIssueCategory(IssueCategory.SECURITY);
        first.setSeverity(IssueSeverity.HIGH);
        CodeAnalysisIssue second = issue(101L, analysis);
        second.setIssueCategory(IssueCategory.STYLE);
        second.setSeverity(IssueSeverity.INFO);

        assertThat(PrIssueLineageFingerprint.computePersisted(first))
                .isEqualTo(PrIssueLineageFingerprint.computePersisted(second));
    }

    private static Project project(Long id) throws Exception {
        Project project = new Project();
        setId(project, id);
        return project;
    }

    private static CodeAnalysis analysis(
            Long id, Project project, Long prNumber, int version
    ) throws Exception {
        CodeAnalysis analysis = new CodeAnalysis();
        setId(analysis, id);
        analysis.setProject(project);
        analysis.setPrNumber(prNumber);
        analysis.setPrVersion(version);
        return analysis;
    }

    private static CodeAnalysisIssue issue(Long id, CodeAnalysis analysis) throws Exception {
        CodeAnalysisIssue issue = new CodeAnalysisIssue();
        setId(issue, id);
        issue.setFilePath("src/App.java");
        issue.setLineNumber(12);
        issue.setLineHash("stable-line-hash");
        issue.setCodeSnippet("dangerous();");
        issue.setIssueScope(IssueScope.LINE);
        issue.setTitle("Unsafe call");
        issue.setReason("Unsafe call reaches a visible runtime failure");
        issue.setSeverity(IssueSeverity.HIGH);
        issue.setIssueCategory(IssueCategory.BUG_RISK);
        analysis.addIssue(issue);
        return issue;
    }

    private static void setId(Object target, Long id) throws Exception {
        Field field = target.getClass().getDeclaredField("id");
        field.setAccessible(true);
        field.set(target, id);
    }
}
