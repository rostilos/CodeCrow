package org.rostilos.codecrow.commitgraph.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.commitgraph.dag.CommitRangeContext;
import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchRepository;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.rostilos.codecrow.vcsclient.model.VcsCommit;

import java.lang.reflect.Field;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class BranchCommitServiceTest {

    @Mock private VcsClientProvider vcsClientProvider;
    @Mock private AnalyzedCommitService analyzedCommitService;
    @Mock private BranchRepository branchRepository;
    @Mock private VcsClient vcsClient;
    @Mock private VcsRepoInfo vcsRepoInfo;

    private BranchCommitService service;

    private static void setId(Object entity, Long id) throws Exception {
        Field f = entity.getClass().getDeclaredField("id");
        f.setAccessible(true);
        f.set(entity, id);
    }

    /** Create a Project with mocked VcsRepoInfo for workspace/slug access */
    private Project projectWithVcsInfo(Long id) throws Exception {
        Project project = spy(new Project());
        setId(project, id);
        doReturn(vcsRepoInfo).when(project).getEffectiveVcsRepoInfo();
        lenient().when(vcsRepoInfo.getRepoWorkspace()).thenReturn("ws");
        lenient().when(vcsRepoInfo.getRepoSlug()).thenReturn("repo");
        return project;
    }

    private static VcsCommit commit(String hash) {
        return new VcsCommit(hash, "msg", null, null, null, null);
    }

    @BeforeEach
    void setUp() {
        service = new BranchCommitService(vcsClientProvider, analyzedCommitService, branchRepository);
    }

    // ── First analysis (no lastKnownHeadCommit) ──────────────────────────

    @Test
    void resolveCommitRange_noBranch_shouldReturnFirstAnalysis() throws Exception {
        Project project = new Project();
        setId(project, 1L);

        when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                .thenReturn(Optional.empty());

        CommitRangeContext ctx = service.resolveCommitRange(
                project, new VcsConnection(), "main", "abc123");

        assertThat(ctx.skipAnalysis()).isFalse();
        assertThat(ctx.diffBase()).isNull();
        assertThat(ctx.unanalyzedCommits()).containsExactly("abc123");
    }

    @Test
    void resolveCommitRange_branchWithNullLastKnownHead_shouldReturnFirstAnalysis() throws Exception {
        Project project = new Project();
        setId(project, 1L);

        Branch branch = new Branch();
        branch.setLastKnownHeadCommit(null);
        when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                .thenReturn(Optional.of(branch));

        CommitRangeContext ctx = service.resolveCommitRange(
                project, new VcsConnection(), "main", "abc123");

        assertThat(ctx.skipAnalysis()).isFalse();
        assertThat(ctx.diffBase()).isNull();
        assertThat(ctx.unanalyzedCommits()).containsExactly("abc123");
    }

    // ── Same commit ──────────────────────────────────────────────────────

    @Test
    void resolveCommitRange_sameCommitAlreadyAnalyzed_shouldSkip() throws Exception {
        Project project = new Project();
        setId(project, 1L);

        Branch branch = new Branch();
        branch.setLastKnownHeadCommit("abc123");
        when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                .thenReturn(Optional.of(branch));
        when(analyzedCommitService.isAnalyzed(1L, "abc123")).thenReturn(true);

        CommitRangeContext ctx = service.resolveCommitRange(
                project, new VcsConnection(), "main", "abc123");

        assertThat(ctx.skipAnalysis()).isTrue();
        assertThat(ctx.unanalyzedCommits()).isEmpty();
    }

    @Test
    void resolveCommitRange_sameCommitNotAnalyzed_shouldReturnSingleCommit() throws Exception {
        Project project = new Project();
        setId(project, 1L);

        Branch branch = new Branch();
        branch.setLastKnownHeadCommit("abc123");
        when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                .thenReturn(Optional.of(branch));
        when(analyzedCommitService.isAnalyzed(1L, "abc123")).thenReturn(false);

        CommitRangeContext ctx = service.resolveCommitRange(
                project, new VcsConnection(), "main", "abc123");

        assertThat(ctx.skipAnalysis()).isFalse();
        assertThat(ctx.diffBase()).isNull();
        assertThat(ctx.unanalyzedCommits()).containsExactly("abc123");
    }

    // ── Normal case: new commits ─────────────────────────────────────────

    @Test
    void resolveCommitRange_normalCase_shouldResolveNewCommits() throws Exception {
        Project project = projectWithVcsInfo(1L);

        Branch branch = new Branch();
        branch.setLastKnownHeadCommit("old-head");
        when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                .thenReturn(Optional.of(branch));

        VcsConnection conn = new VcsConnection();
        when(vcsClientProvider.getClient(conn)).thenReturn(vcsClient);

        when(vcsClient.getCommitHistory("ws", "repo", "main", 100))
                .thenReturn(List.of(
                        commit("new3"), commit("new2"), commit("new1"), commit("old-head")
                ));

        when(analyzedCommitService.filterUnanalyzed(eq(1L), anyList()))
                .thenReturn(List.of("new1", "new3"));

        CommitRangeContext ctx = service.resolveCommitRange(project, conn, "main", "new3");

        assertThat(ctx.skipAnalysis()).isFalse();
        assertThat(ctx.diffBase()).isEqualTo("old-head");
        assertThat(ctx.unanalyzedCommits()).containsExactly("new1", "new3");
    }

    @Test
    void resolveCommitRange_allCommitsAlreadyAnalyzed_shouldSkip() throws Exception {
        Project project = projectWithVcsInfo(1L);

        Branch branch = new Branch();
        branch.setLastKnownHeadCommit("old-head");
        when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                .thenReturn(Optional.of(branch));

        VcsConnection conn = new VcsConnection();
        when(vcsClientProvider.getClient(conn)).thenReturn(vcsClient);

        when(vcsClient.getCommitHistory("ws", "repo", "main", 100))
                .thenReturn(List.of(commit("new1"), commit("old-head")));

        when(analyzedCommitService.filterUnanalyzed(eq(1L), anyList()))
                .thenReturn(List.of());

        CommitRangeContext ctx = service.resolveCommitRange(project, conn, "main", "new1");

        assertThat(ctx.skipAnalysis()).isTrue();
    }

    // ── Edge cases ───────────────────────────────────────────────────────

    @Test
    void resolveCommitRange_vcsReturnsNull_shouldFallBackToHeadOnly() throws Exception {
        Project project = projectWithVcsInfo(1L);

        Branch branch = new Branch();
        branch.setLastKnownHeadCommit("old-head");
        when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                .thenReturn(Optional.of(branch));

        VcsConnection conn = new VcsConnection();
        when(vcsClientProvider.getClient(conn)).thenReturn(vcsClient);
        when(vcsClient.getCommitHistory("ws", "repo", "main", 100))
                .thenReturn(null);

        CommitRangeContext ctx = service.resolveCommitRange(project, conn, "main", "new-head");

        assertThat(ctx.skipAnalysis()).isFalse();
        assertThat(ctx.unanalyzedCommits()).containsExactly("new-head");
        assertThat(ctx.diffBase()).isEqualTo("old-head");
    }

    @Test
    void resolveCommitRange_vcsReturnsEmpty_shouldFallBackToHeadOnly() throws Exception {
        Project project = projectWithVcsInfo(1L);

        Branch branch = new Branch();
        branch.setLastKnownHeadCommit("old-head");
        when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                .thenReturn(Optional.of(branch));

        VcsConnection conn = new VcsConnection();
        when(vcsClientProvider.getClient(conn)).thenReturn(vcsClient);
        when(vcsClient.getCommitHistory("ws", "repo", "main", 100))
                .thenReturn(List.of());

        CommitRangeContext ctx = service.resolveCommitRange(project, conn, "main", "new-head");

        assertThat(ctx.unanalyzedCommits()).containsExactly("new-head");
    }

    @Test
    void resolveCommitRange_lastKnownHeadNotInWindow_shouldFallBackToHeadOnly() throws Exception {
        Project project = projectWithVcsInfo(1L);

        Branch branch = new Branch();
        branch.setLastKnownHeadCommit("very-old-head");
        when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                .thenReturn(Optional.of(branch));

        VcsConnection conn = new VcsConnection();
        when(vcsClientProvider.getClient(conn)).thenReturn(vcsClient);

        when(vcsClient.getCommitHistory("ws", "repo", "main", 100))
                .thenReturn(List.of(commit("c3"), commit("c2"), commit("c1")));

        CommitRangeContext ctx = service.resolveCommitRange(project, conn, "main", "c3");

        assertThat(ctx.unanalyzedCommits()).containsExactly("c3");
        assertThat(ctx.diffBase()).isEqualTo("very-old-head");
    }

    @Test
    void resolveCommitRange_vcsThrows_shouldFallBackToHeadOnly() throws Exception {
        Project project = projectWithVcsInfo(1L);

        Branch branch = new Branch();
        branch.setLastKnownHeadCommit("old-head");
        when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                .thenReturn(Optional.of(branch));

        VcsConnection conn = new VcsConnection();
        when(vcsClientProvider.getClient(conn)).thenReturn(vcsClient);
        when(vcsClient.getCommitHistory(anyString(), anyString(), anyString(), anyInt()))
                .thenThrow(new RuntimeException("VCS API down"));

        CommitRangeContext ctx = service.resolveCommitRange(project, conn, "main", "new-head");

        assertThat(ctx.skipAnalysis()).isFalse();
        assertThat(ctx.unanalyzedCommits()).containsExactly("new-head");
        assertThat(ctx.diffBase()).isEqualTo("old-head");
    }
}
