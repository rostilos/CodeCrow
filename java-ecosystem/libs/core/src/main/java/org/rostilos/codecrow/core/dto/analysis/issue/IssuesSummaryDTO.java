package org.rostilos.codecrow.core.dto.analysis.issue;

import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;

import java.util.List;

public record IssuesSummaryDTO(
    int totalIssues,
    int highCount,
    int mediumCount,
    int lowCount,
    int securityCount,
    int qualityCount,
    int performanceCount,
    int styleCount
) {
    public static IssuesSummaryDTO fromIssuesDTOs(List<IssueDTO> issueDTOS) {
        return new IssuesSummaryDTO(
                issueDTOS.size(),
                (int) issueDTOS.stream().filter(i -> i.severity().equals(IssueSeverity.HIGH.toString())).count(),
                (int) issueDTOS.stream().filter(i -> i.severity().equals(IssueSeverity.MEDIUM.toString())).count(),
                (int) issueDTOS.stream().filter(i -> i.severity().equals(IssueSeverity.LOW.toString())).count(),
                (int) issueDTOS.stream().filter(i -> i.issueCategory() != null && i.issueCategory().toLowerCase().contains("security")).count(),
                (int) issueDTOS.stream().filter(i -> i.issueCategory() != null && i.issueCategory().toLowerCase().contains("quality")).count(),
                (int) issueDTOS.stream().filter(i -> i.issueCategory() != null && i.issueCategory().toLowerCase().contains("performance")).count(),
                (int) issueDTOS.stream().filter(i -> i.issueCategory() != null && i.issueCategory().toLowerCase().contains("style")).count()
        );
    }
}
