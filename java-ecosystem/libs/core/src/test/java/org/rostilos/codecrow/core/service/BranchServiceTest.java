package org.rostilos.codecrow.core.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.branch.BranchIssue;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchIssueRepository;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchRepository;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;

import java.lang.reflect.Field;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
@DisplayName("BranchService")
class BranchServiceTest {

    @Mock
    private BranchRepository branchRepository;
    @Mock
    private BranchIssueRepository branchIssueRepository;
    @Mock
    private CodeAnalysisRepository codeAnalysisRepository;

    private BranchService branchService;

    @BeforeEach
    void setUp() {
        branchService = new BranchService(branchRepository, branchIssueRepository, codeAnalysisRepository);
    }

    @Nested
    @DisplayName("findByProjectIdAndBranchName()")
    class FindByProjectIdAndBranchNameTests {

        @Test
        @DisplayName("should return branch when found")
        void shouldReturnBranchWhenFound() {
            Branch branch = new Branch();
            setField(branch, "id", 1L);
            branch.setBranchName("main");
            when(branchRepository.findByProjectIdAndBranchName(10L, "main")).thenReturn(Optional.of(branch));

            Optional<Branch> result = branchService.findByProjectIdAndBranchName(10L, "main");

            assertThat(result).isPresent();
            assertThat(result.get().getBranchName()).isEqualTo("main");
            verify(branchRepository).findByProjectIdAndBranchName(10L, "main");
        }

        @Test
        @DisplayName("should return empty when not found")
        void shouldReturnEmptyWhenNotFound() {
            when(branchRepository.findByProjectIdAndBranchName(10L, "unknown")).thenReturn(Optional.empty());

            Optional<Branch> result = branchService.findByProjectIdAndBranchName(10L, "unknown");

            assertThat(result).isEmpty();
        }
    }

    @Nested
    @DisplayName("findByProjectId()")
    class FindByProjectIdTests {

        @Test
        @DisplayName("should return branches for project")
        void shouldReturnBranchesForProject() {
            Branch branch1 = createBranch(1L, "main");
            Branch branch2 = createBranch(2L, "develop");
            when(branchRepository.findByProjectId(10L)).thenReturn(List.of(branch1, branch2));

            List<Branch> result = branchService.findByProjectId(10L);

            assertThat(result).hasSize(2);
            verify(branchRepository).findByProjectId(10L);
        }

        @Test
        @DisplayName("should return empty list when no branches")
        void shouldReturnEmptyListWhenNoBranches() {
            when(branchRepository.findByProjectId(10L)).thenReturn(List.of());

            List<Branch> result = branchService.findByProjectId(10L);

            assertThat(result).isEmpty();
        }
    }

    @Nested
    @DisplayName("findIssuesByBranchId()")
    class FindIssuesByBranchIdTests {

        @Test
        @DisplayName("should return issues for branch")
        void shouldReturnIssuesForBranch() {
            BranchIssue issue1 = new BranchIssue();
            BranchIssue issue2 = new BranchIssue();
            when(branchIssueRepository.findByBranchId(1L)).thenReturn(List.of(issue1, issue2));

            List<BranchIssue> result = branchService.findIssuesByBranchId(1L);

            assertThat(result).hasSize(2);
            verify(branchIssueRepository).findByBranchId(1L);
        }
    }

    @Nested
    @DisplayName("getBranchStats()")
    class GetBranchStatsTests {

        @Test
        @DisplayName("should return empty stats when branch not found")
        void shouldReturnEmptyStatsWhenBranchNotFound() {
            when(branchRepository.findByProjectIdAndBranchName(10L, "main")).thenReturn(Optional.empty());

            BranchService.BranchStats stats = branchService.getBranchStats(10L, "main");

            assertThat(stats.getTotalIssues()).isZero();
            assertThat(stats.getHighSeverityCount()).isZero();
            assertThat(stats.getMediumSeverityCount()).isZero();
            assertThat(stats.getLowSeverityCount()).isZero();
            assertThat(stats.getInfoSeverityCount()).isZero();
            assertThat(stats.getResolvedCount()).isZero();
            assertThat(stats.getTotalAnalyses()).isZero();
            assertThat(stats.getMostProblematicFiles()).isEmpty();
            assertThat(stats.getLastAnalysisDate()).isNull();
            assertThat(stats.getFirstAnalysisDate()).isNull();
        }

        @Test
        @DisplayName("should return stats when branch has issues")
        void shouldReturnStatsWhenBranchHasIssues() {
            Branch branch = createBranch(1L, "main");

            List<BranchIssue> issues = List.of(
                    createBranchIssue(1L, IssueSeverity.HIGH, false, "/src/Main.java"),
                    createBranchIssue(2L, IssueSeverity.HIGH, false, "/src/Main.java"),
                    createBranchIssue(3L, IssueSeverity.MEDIUM, false, "/src/Service.java"),
                    createBranchIssue(4L, IssueSeverity.LOW, false, "/src/Utils.java"),
                    createBranchIssue(5L, IssueSeverity.INFO, false, "/src/Config.java"),
                    createBranchIssue(6L, IssueSeverity.HIGH, true, "/src/Fixed.java")
            );

            when(branchRepository.findByProjectIdAndBranchName(10L, "main")).thenReturn(Optional.of(branch));
            when(branchIssueRepository.findByBranchId(1L)).thenReturn(issues);

            BranchService.BranchStats stats = branchService.getBranchStats(10L, "main");

            assertThat(stats.getTotalIssues()).isEqualTo(5);
            assertThat(stats.getHighSeverityCount()).isEqualTo(2);
            assertThat(stats.getMediumSeverityCount()).isEqualTo(1);
            assertThat(stats.getLowSeverityCount()).isEqualTo(1);
            assertThat(stats.getInfoSeverityCount()).isEqualTo(1);
            assertThat(stats.getResolvedCount()).isEqualTo(1);
            assertThat(stats.getTotalAnalyses()).isEqualTo(1);
            assertThat(stats.getMostProblematicFiles()).isNotEmpty();
            assertThat(stats.getLastAnalysisDate()).isNotNull();
            assertThat(stats.getFirstAnalysisDate()).isNotNull();
        }

        @Test
        @DisplayName("should return stats with no issues")
        void shouldReturnStatsWithNoIssues() {
            Branch branch = createBranch(1L, "main");
            when(branchRepository.findByProjectIdAndBranchName(10L, "main")).thenReturn(Optional.of(branch));
            when(branchIssueRepository.findByBranchId(1L)).thenReturn(List.of());

            BranchService.BranchStats stats = branchService.getBranchStats(10L, "main");

            assertThat(stats.getTotalIssues()).isZero();
            assertThat(stats.getMostProblematicFiles()).isEmpty();
        }

        @Test
        @DisplayName("should limit most problematic files to 10")
        void shouldLimitMostProblematicFilesToTen() {
            Branch branch = createBranch(1L, "main");
            List<BranchIssue> issues = List.of(
                    createBranchIssue(1L, IssueSeverity.HIGH, false, "/src/File1.java"),
                    createBranchIssue(2L, IssueSeverity.HIGH, false, "/src/File2.java"),
                    createBranchIssue(3L, IssueSeverity.HIGH, false, "/src/File3.java"),
                    createBranchIssue(4L, IssueSeverity.HIGH, false, "/src/File4.java"),
                    createBranchIssue(5L, IssueSeverity.HIGH, false, "/src/File5.java"),
                    createBranchIssue(6L, IssueSeverity.HIGH, false, "/src/File6.java"),
                    createBranchIssue(7L, IssueSeverity.HIGH, false, "/src/File7.java"),
                    createBranchIssue(8L, IssueSeverity.HIGH, false, "/src/File8.java"),
                    createBranchIssue(9L, IssueSeverity.HIGH, false, "/src/File9.java"),
                    createBranchIssue(10L, IssueSeverity.HIGH, false, "/src/File10.java"),
                    createBranchIssue(11L, IssueSeverity.HIGH, false, "/src/File11.java"),
                    createBranchIssue(12L, IssueSeverity.HIGH, false, "/src/File12.java")
            );

            when(branchRepository.findByProjectIdAndBranchName(10L, "main")).thenReturn(Optional.of(branch));
            when(branchIssueRepository.findByBranchId(1L)).thenReturn(issues);

            BranchService.BranchStats stats = branchService.getBranchStats(10L, "main");

            assertThat(stats.getMostProblematicFiles()).hasSize(10);
        }
    }

    @Nested
    @DisplayName("getBranchAnalysisHistory()")
    class GetBranchAnalysisHistoryTests {

        @Test
        @DisplayName("should return analysis history for branch")
        void shouldReturnAnalysisHistoryForBranch() {
            CodeAnalysis analysis1 = new CodeAnalysis();
            CodeAnalysis analysis2 = new CodeAnalysis();
            when(codeAnalysisRepository.findByProjectIdAndBranchName(10L, "main"))
                    .thenReturn(List.of(analysis1, analysis2));

            List<CodeAnalysis> result = branchService.getBranchAnalysisHistory(10L, "main");

            assertThat(result).hasSize(2);
            verify(codeAnalysisRepository).findByProjectIdAndBranchName(10L, "main");
        }
    }

    @Nested
    @DisplayName("BranchStats")
    class BranchStatsTests {

        @Test
        @DisplayName("should create BranchStats with all fields")
        void shouldCreateBranchStatsWithAllFields() {
            OffsetDateTime lastDate = OffsetDateTime.now();
            OffsetDateTime firstDate = OffsetDateTime.now().minusDays(30);
            List<Object[]> files = new java.util.ArrayList<>();
            files.add(new Object[]{"/src/Main.java", 5L});

            BranchService.BranchStats stats = new BranchService.BranchStats(
                    100, 25, 35, 30, 10, 15, 5, files, lastDate, firstDate
            );

            assertThat(stats.getTotalIssues()).isEqualTo(100);
            assertThat(stats.getHighSeverityCount()).isEqualTo(25);
            assertThat(stats.getMediumSeverityCount()).isEqualTo(35);
            assertThat(stats.getLowSeverityCount()).isEqualTo(30);
            assertThat(stats.getInfoSeverityCount()).isEqualTo(10);
            assertThat(stats.getResolvedCount()).isEqualTo(15);
            assertThat(stats.getTotalAnalyses()).isEqualTo(5);
            assertThat(stats.getMostProblematicFiles()).isEqualTo(files);
            assertThat(stats.getLastAnalysisDate()).isEqualTo(lastDate);
            assertThat(stats.getFirstAnalysisDate()).isEqualTo(firstDate);
        }
    }

    // Helper methods

    private Branch createBranch(Long id, String name) {
        Branch branch = new Branch();
        setField(branch, "id", id);
        branch.setBranchName(name);
        return branch;
    }

    private BranchIssue createBranchIssue(Long id, IssueSeverity severity, boolean resolved, String filePath) {
        CodeAnalysisIssue codeIssue = new CodeAnalysisIssue();
        codeIssue.setFilePath(filePath);

        BranchIssue issue = new BranchIssue();
        setField(issue, "id", id);
        issue.setSeverity(severity);
        issue.setResolved(resolved);
        issue.setCodeAnalysisIssue(codeIssue);
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
