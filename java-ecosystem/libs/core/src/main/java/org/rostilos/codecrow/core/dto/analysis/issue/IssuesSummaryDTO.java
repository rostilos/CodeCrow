package org.rostilos.codecrow.core.dto.analysis.issue;

import org.rostilos.codecrow.core.model.codeanalysis.IssueCategory;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;

import java.util.List;

public record IssuesSummaryDTO(
    int totalIssues,
    int highCount,
    int mediumCount,
    int lowCount,
    int infoCount,
    int securityCount,
    int qualityCount,
    int performanceCount,
    int styleCount,
    int bugRiskCount,
    int documentationCount,
    int bestPracticesCount,
    int errorHandlingCount,
    int testingCount,
    int architectureCount
) {
    public static IssuesSummaryDTO fromIssuesDTOs(List<IssueDTO> issueDTOS) {
        return new IssuesSummaryDTO(
                issueDTOS.size(),
                (int) issueDTOS.stream().filter(i -> i.severity().equals(IssueSeverity.HIGH.toString())).count(),
                (int) issueDTOS.stream().filter(i -> i.severity().equals(IssueSeverity.MEDIUM.toString())).count(),
                (int) issueDTOS.stream().filter(i -> i.severity().equals(IssueSeverity.LOW.toString())).count(),
                (int) issueDTOS.stream().filter(i -> i.severity().equals(IssueSeverity.INFO.toString())).count(),
                (int) issueDTOS.stream().filter(i -> IssueCategory.SECURITY.name().equals(i.issueCategory())).count(),
                (int) issueDTOS.stream().filter(i -> IssueCategory.CODE_QUALITY.name().equals(i.issueCategory())).count(),
                (int) issueDTOS.stream().filter(i -> IssueCategory.PERFORMANCE.name().equals(i.issueCategory())).count(),
                (int) issueDTOS.stream().filter(i -> IssueCategory.STYLE.name().equals(i.issueCategory())).count(),
                (int) issueDTOS.stream().filter(i -> IssueCategory.BUG_RISK.name().equals(i.issueCategory())).count(),
                (int) issueDTOS.stream().filter(i -> IssueCategory.DOCUMENTATION.name().equals(i.issueCategory())).count(),
                (int) issueDTOS.stream().filter(i -> IssueCategory.BEST_PRACTICES.name().equals(i.issueCategory())).count(),
                (int) issueDTOS.stream().filter(i -> IssueCategory.ERROR_HANDLING.name().equals(i.issueCategory())).count(),
                (int) issueDTOS.stream().filter(i -> IssueCategory.TESTING.name().equals(i.issueCategory())).count(),
                (int) issueDTOS.stream().filter(i -> IssueCategory.ARCHITECTURE.name().equals(i.issueCategory())).count()
        );
    }
}
