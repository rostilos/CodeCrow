package org.rostilos.codecrow.webserver.analytics.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.branch.BranchIssue;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;
import org.rostilos.codecrow.core.service.BranchService;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.webserver.project.service.ProjectService;

import java.lang.reflect.Field;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.data.Offset.offset;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
@DisplayName("ProjectAnalyticsService")
class ProjectAnalyticsServiceTest {

    @Mock
    private CodeAnalysisService codeAnalysisService;
    @Mock
    private BranchService branchService;
    @Mock
    private ProjectService projectService;
    @Mock
    private CodeAnalysisRepository codeAnalysisRepository;

    private ProjectAnalyticsService service;

    @BeforeEach
    void setUp() {
        service = new ProjectAnalyticsService(
                codeAnalysisService,
                branchService,
                projectService,
                codeAnalysisRepository
        );
    }

    @Test
    @DisplayName("branch issues trend uses branch issue inventory snapshots")
    void branchIssuesTrendUsesBranchIssueInventorySnapshots() {
        OffsetDateTime now = OffsetDateTime.now();
        Branch branch = createBranch(7L, "main");
        List<BranchIssue> issues = List.of(
                createBranchIssue(IssueSeverity.HIGH, false, now.minusDays(20), null),
                createBranchIssue(IssueSeverity.MEDIUM, true, now.minusDays(10), now.minusDays(3))
        );
        when(branchService.findByProjectIdAndBranchName(42L, "main")).thenReturn(Optional.of(branch));
        when(branchService.findIssuesByBranchId(7L)).thenReturn(issues);

        List<ProjectAnalyticsService.BranchIssuesTrendPoint> trend =
                service.getBranchIssuesTrend(42L, "main", 0, 30);

        assertThat(trend).isNotEmpty();
        assertThat(trend.get(0).getTotalIssues()).isZero();

        ProjectAnalyticsService.BranchIssuesTrendPoint latest = trend.get(trend.size() - 1);
        assertThat(latest.getTotalIssues()).isEqualTo(1);
        assertThat(latest.getHighSeverityCount()).isEqualTo(1);
        assertThat(latest.getMediumSeverityCount()).isZero();
        assertThat(latest.getLowSeverityCount()).isZero();
        verifyNoInteractions(codeAnalysisRepository, codeAnalysisService, projectService);
    }

    @Test
    @DisplayName("branch resolved trend calculates resolved rate from branch issues")
    void branchResolvedTrendCalculatesResolvedRateFromBranchIssues() {
        OffsetDateTime now = OffsetDateTime.now();
        Branch branch = createBranch(7L, "main");
        List<BranchIssue> issues = List.of(
                createBranchIssue(IssueSeverity.HIGH, false, now.minusDays(20), null),
                createBranchIssue(IssueSeverity.MEDIUM, true, now.minusDays(10), now.minusDays(3))
        );
        when(branchService.findByProjectIdAndBranchName(42L, "main")).thenReturn(Optional.of(branch));
        when(branchService.findIssuesByBranchId(7L)).thenReturn(issues);

        List<ProjectAnalyticsService.ResolvedTrendPoint> trend =
                service.getResolvedTrend(42L, "main", 0, 30);

        assertThat(trend).isNotEmpty();
        assertThat(trend.get(0).getResolvedRate()).isZero();

        ProjectAnalyticsService.ResolvedTrendPoint latest = trend.get(trend.size() - 1);
        assertThat(latest.getResolvedCount()).isEqualTo(1);
        assertThat(latest.getTotalIssues()).isEqualTo(2);
        assertThat(latest.getResolvedRate()).isCloseTo(0.5, offset(0.0001));
        verifyNoInteractions(codeAnalysisRepository, codeAnalysisService, projectService);
    }

    private Branch createBranch(Long id, String name) {
        Branch branch = new Branch();
        setField(branch, "id", id);
        branch.setBranchName(name);
        return branch;
    }

    private BranchIssue createBranchIssue(
            IssueSeverity severity,
            boolean resolved,
            OffsetDateTime createdAt,
            OffsetDateTime resolvedAt
    ) {
        BranchIssue issue = new BranchIssue();
        issue.setSeverity(severity);
        issue.setResolved(resolved);
        issue.setFilePath("/src/Main.java");
        setField(issue, "createdAt", createdAt);
        if (resolvedAt != null) {
            issue.setResolvedAt(resolvedAt);
        }
        return issue;
    }

    private void setField(Object obj, String fieldName, Object value) {
        try {
            Field field = obj.getClass().getDeclaredField(fieldName);
            field.setAccessible(true);
            field.set(obj, value);
        } catch (Exception e) {
            throw new RuntimeException("Failed to set field " + fieldName, e);
        }
    }
}
