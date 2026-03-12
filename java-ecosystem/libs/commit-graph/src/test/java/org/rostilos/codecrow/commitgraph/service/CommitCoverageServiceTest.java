package org.rostilos.codecrow.commitgraph.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.commitgraph.persistence.AnalyzedCommitRepository;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.pullrequest.PullRequest;
import org.rostilos.codecrow.core.model.pullrequest.PullRequestState;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;
import org.rostilos.codecrow.core.persistence.repository.pullrequest.PullRequestRepository;

import java.lang.reflect.Field;
import java.util.Collections;
import java.util.List;
import java.util.Optional;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class CommitCoverageServiceTest {

    @Mock private PullRequestRepository pullRequestRepository;
    @Mock private CodeAnalysisRepository codeAnalysisRepository;
    @Mock private AnalyzedCommitRepository analyzedCommitRepository;

    private CommitCoverageService service;

    private static void setId(Object entity, Long id) throws Exception {
        Field f = entity.getClass().getDeclaredField("id");
        f.setAccessible(true);
        f.set(entity, id);
    }

    @BeforeEach
    void setUp() {
        service = new CommitCoverageService(
                pullRequestRepository, codeAnalysisRepository, analyzedCommitRepository);
    }

    // ── Empty/null inputs ────────────────────────────────────────────────

    @Test
    void checkCoverage_nullCommits_shouldReturnFullyCovered() {
        CommitCoverageService.CoverageResult result = service.checkCoverage(1L, "main", null);

        assertThat(result.status()).isEqualTo(CommitCoverageService.CoverageStatus.FULLY_COVERED);
        assertThat(result.uncoveredCommits()).isEmpty();
        assertThat(result.requiresAnalysis()).isFalse();
    }

    @Test
    void checkCoverage_emptyCommits_shouldReturnFullyCovered() {
        CommitCoverageService.CoverageResult result = service.checkCoverage(1L, "main", List.of());

        assertThat(result.status()).isEqualTo(CommitCoverageService.CoverageStatus.FULLY_COVERED);
        assertThat(result.requiresAnalysis()).isFalse();
    }

    // ── Tier 1: analyzed_commit table covers everything ──────────────────

    @Test
    void checkCoverage_allInAnalyzedCommitTable_shouldReturnFullyCovered() {
        when(analyzedCommitRepository.findAnalyzedHashesByProjectIdAndCommitHashIn(
                eq(1L), anyList())).thenReturn(Set.of("aaa", "bbb"));

        CommitCoverageService.CoverageResult result = service.checkCoverage(
                1L, "main", List.of("aaa", "bbb"));

        assertThat(result.status()).isEqualTo(CommitCoverageService.CoverageStatus.FULLY_COVERED);
        assertThat(result.requiresAnalysis()).isFalse();
        verifyNoInteractions(pullRequestRepository); // tier 2 never reached
    }

    // ── Tier 2: PR coverage ──────────────────────────────────────────────

    @Test
    void checkCoverage_noPRs_shouldReturnNotCovered() {
        when(analyzedCommitRepository.findAnalyzedHashesByProjectIdAndCommitHashIn(
                eq(1L), anyList())).thenReturn(Set.of());

        when(pullRequestRepository.findByProjectIdAndTargetBranchNameAndStateIn(
                eq(1L), eq("main"), anyList())).thenReturn(List.of());

        CommitCoverageService.CoverageResult result = service.checkCoverage(
                1L, "main", List.of("aaa"));

        assertThat(result.status()).isEqualTo(CommitCoverageService.CoverageStatus.NOT_COVERED);
        assertThat(result.uncoveredCommits()).containsExactly("aaa");
        assertThat(result.requiresAnalysis()).isTrue();
    }

    @Test
    void checkCoverage_prCoversAllCommits_shouldReturnFullyCovered() throws Exception {
        when(analyzedCommitRepository.findAnalyzedHashesByProjectIdAndCommitHashIn(
                eq(1L), anyList())).thenReturn(Set.of());

        PullRequest pr = new PullRequest();
        setId(pr, 10L);
        pr.setCommitHash("aaa");
        pr.setPrNumber(42);
        pr.setState(PullRequestState.OPEN);
        when(pullRequestRepository.findByProjectIdAndTargetBranchNameAndStateIn(
                eq(1L), eq("main"), anyList())).thenReturn(List.of(pr));

        CodeAnalysis analysis = new CodeAnalysis();
        when(codeAnalysisRepository.findByProjectIdAndCommitHashAndPrNumber(1L, "aaa", 42))
                .thenReturn(Optional.of(analysis));

        CommitCoverageService.CoverageResult result = service.checkCoverage(
                1L, "main", List.of("aaa"));

        assertThat(result.status()).isEqualTo(CommitCoverageService.CoverageStatus.FULLY_COVERED);
    }

    @Test
    void checkCoverage_prCoversPartially_shouldReturnPartiallyCovered() throws Exception {
        when(analyzedCommitRepository.findAnalyzedHashesByProjectIdAndCommitHashIn(
                eq(1L), anyList())).thenReturn(Set.of());

        PullRequest pr = new PullRequest();
        setId(pr, 10L);
        pr.setCommitHash("aaa");
        pr.setPrNumber(42);
        pr.setState(PullRequestState.MERGED);
        when(pullRequestRepository.findByProjectIdAndTargetBranchNameAndStateIn(
                eq(1L), eq("main"), anyList())).thenReturn(List.of(pr));

        CodeAnalysis analysis = new CodeAnalysis();
        when(codeAnalysisRepository.findByProjectIdAndCommitHashAndPrNumber(1L, "aaa", 42))
                .thenReturn(Optional.of(analysis));

        CommitCoverageService.CoverageResult result = service.checkCoverage(
                1L, "main", List.of("aaa", "bbb"));

        assertThat(result.status()).isEqualTo(CommitCoverageService.CoverageStatus.PARTIALLY_COVERED);
        assertThat(result.uncoveredCommits()).containsExactly("bbb");
        assertThat(result.requiresAnalysis()).isTrue();
    }

    @Test
    void checkCoverage_prWithNullCommitHash_shouldNotThrow() throws Exception {
        when(analyzedCommitRepository.findAnalyzedHashesByProjectIdAndCommitHashIn(
                eq(1L), anyList())).thenReturn(Set.of());

        PullRequest pr = new PullRequest();
        setId(pr, 10L);
        pr.setCommitHash(null);
        pr.setPrNumber(42);
        pr.setState(PullRequestState.OPEN);
        when(pullRequestRepository.findByProjectIdAndTargetBranchNameAndStateIn(
                eq(1L), eq("main"), anyList())).thenReturn(List.of(pr));

        CommitCoverageService.CoverageResult result = service.checkCoverage(
                1L, "main", List.of("aaa"));

        assertThat(result.status()).isEqualTo(CommitCoverageService.CoverageStatus.NOT_COVERED);
    }

    // ── Mixed tier 1 + tier 2 ────────────────────────────────────────────

    @Test
    void checkCoverage_tier1CoversSome_tier2CoversRest_shouldReturnFullyCovered() throws Exception {
        // Tier 1 covers "aaa"
        when(analyzedCommitRepository.findAnalyzedHashesByProjectIdAndCommitHashIn(
                eq(1L), anyList())).thenReturn(Set.of("aaa"));

        // Tier 2 covers "bbb" via PR
        PullRequest pr = new PullRequest();
        setId(pr, 10L);
        pr.setCommitHash("bbb");
        pr.setPrNumber(42);
        pr.setState(PullRequestState.OPEN);
        when(pullRequestRepository.findByProjectIdAndTargetBranchNameAndStateIn(
                eq(1L), eq("main"), anyList())).thenReturn(List.of(pr));

        CodeAnalysis analysis = new CodeAnalysis();
        when(codeAnalysisRepository.findByProjectIdAndCommitHashAndPrNumber(1L, "bbb", 42))
                .thenReturn(Optional.of(analysis));

        CommitCoverageService.CoverageResult result = service.checkCoverage(
                1L, "main", List.of("aaa", "bbb"));

        assertThat(result.status()).isEqualTo(CommitCoverageService.CoverageStatus.FULLY_COVERED);
    }

    // ── CoverageResult and CoverageStatus ────────────────────────────────

    @Test
    void coverageResult_requiresAnalysis_shouldBeCorrect() {
        assertThat(new CommitCoverageService.CoverageResult(
                CommitCoverageService.CoverageStatus.FULLY_COVERED, Collections.emptyList())
                .requiresAnalysis()).isFalse();

        assertThat(new CommitCoverageService.CoverageResult(
                CommitCoverageService.CoverageStatus.PARTIALLY_COVERED, List.of("a"))
                .requiresAnalysis()).isTrue();

        assertThat(new CommitCoverageService.CoverageResult(
                CommitCoverageService.CoverageStatus.NOT_COVERED, List.of("a"))
                .requiresAnalysis()).isTrue();
    }

    @Test
    void coverageStatus_values_shouldExist() {
        assertThat(CommitCoverageService.CoverageStatus.values()).hasSize(3);
        assertThat(CommitCoverageService.CoverageStatus.valueOf("FULLY_COVERED")).isNotNull();
        assertThat(CommitCoverageService.CoverageStatus.valueOf("PARTIALLY_COVERED")).isNotNull();
        assertThat(CommitCoverageService.CoverageStatus.valueOf("NOT_COVERED")).isNotNull();
    }
}
