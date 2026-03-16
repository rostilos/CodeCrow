package org.rostilos.codecrow.analysisengine.service.branch;

import okhttp3.OkHttpClient;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.processor.VcsRepoInfoImpl;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsOperationsService;
import org.rostilos.codecrow.commitgraph.dag.CommitRangeContext;
import org.rostilos.codecrow.core.model.branch.Branch;

import java.io.IOException;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class BranchDiffFetcherTest {

    @Mock private VcsOperationsService operationsService;
    @Mock private OkHttpClient client;

    private BranchDiffFetcher fetcher;
    private VcsRepoInfoImpl vcsRepoInfo;

    @BeforeEach
    void setUp() {
        fetcher = new BranchDiffFetcher();
        vcsRepoInfo = new VcsRepoInfoImpl(null, "ws", "repo");
    }

    private BranchProcessRequest makeRequest(String branchName, String commitHash) {
        BranchProcessRequest req = new BranchProcessRequest();
        req.targetBranchName = branchName;
        req.commitHash = commitHash;
        req.projectId = 1L;
        return req;
    }

    // ── First analysis ───────────────────────────────────────────────────

    @Nested
    class FirstAnalysis {

        @Test
        void noPrNumber_shouldUseCommitDiff() throws IOException {
            BranchProcessRequest request = makeRequest("main", "abc123");
            when(operationsService.getCommitDiff(client, "ws", "repo", "abc123"))
                    .thenReturn("diff content");

            String diff = fetcher.fetchDiff(request, null, CommitRangeContext.firstAnalysis("abc123"),
                    operationsService, client, vcsRepoInfo, null, List.of("abc123"));

            assertThat(diff).isEqualTo("diff content");
        }

        @Test
        void withPrNumber_shouldUsePrDiff() throws IOException {
            BranchProcessRequest request = makeRequest("main", "abc123");
            when(operationsService.getPullRequestDiff(client, "ws", "repo", "42"))
                    .thenReturn("pr diff");

            String diff = fetcher.fetchDiff(request, null, CommitRangeContext.firstAnalysis("abc123"),
                    operationsService, client, vcsRepoInfo, 42L, List.of("abc123"));

            assertThat(diff).isEqualTo("pr diff");
            verify(operationsService, never()).getCommitDiff(any(), any(), any(), any());
        }

        @Test
        void prDiffEmpty_shouldFallBackToCommitDiff() throws IOException {
            BranchProcessRequest request = makeRequest("main", "abc123");
            when(operationsService.getPullRequestDiff(client, "ws", "repo", "42"))
                    .thenReturn("");
            when(operationsService.getCommitDiff(client, "ws", "repo", "abc123"))
                    .thenReturn("commit diff");

            String diff = fetcher.fetchDiff(request, null, CommitRangeContext.firstAnalysis("abc123"),
                    operationsService, client, vcsRepoInfo, 42L, List.of("abc123"));

            assertThat(diff).isEqualTo("commit diff");
        }

        @Test
        void prDiffThrows_shouldFallBackToCommitDiff() throws IOException {
            BranchProcessRequest request = makeRequest("main", "abc123");
            when(operationsService.getPullRequestDiff(client, "ws", "repo", "42"))
                    .thenThrow(new IOException("network error"));
            when(operationsService.getCommitDiff(client, "ws", "repo", "abc123"))
                    .thenReturn("commit diff");

            String diff = fetcher.fetchDiff(request, null, CommitRangeContext.firstAnalysis("abc123"),
                    operationsService, client, vcsRepoInfo, 42L, List.of("abc123"));

            assertThat(diff).isEqualTo("commit diff");
        }
    }

    // ── Subsequent analysis ──────────────────────────────────────────────

    @Nested
    class SubsequentAnalysis {

        private Branch existingBranch;

        @BeforeEach
        void setUp() {
            existingBranch = new Branch();
            existingBranch.setLastSuccessfulCommitHash("prev-success");
        }

        @Test
        void withPrNumber_shouldUsePrDiff() throws IOException {
            BranchProcessRequest request = makeRequest("main", "new-head");
            CommitRangeContext ctx = new CommitRangeContext(List.of("new-head"), "old-head", false);
            when(operationsService.getPullRequestDiff(client, "ws", "repo", "42"))
                    .thenReturn("pr diff");

            String diff = fetcher.fetchDiff(request, existingBranch, ctx,
                    operationsService, client, vcsRepoInfo, 42L, List.of("new-head"));

            assertThat(diff).isEqualTo("pr diff");
        }

        @Test
        void noPr_tier1RangeDiffAvailable_shouldUseRangeDiff() throws IOException {
            BranchProcessRequest request = makeRequest("main", "new-head");
            CommitRangeContext ctx = new CommitRangeContext(List.of("new-head"), "old-head", false);
            when(operationsService.getCommitRangeDiff(client, "ws", "repo", "old-head", "new-head"))
                    .thenReturn("range diff");

            String diff = fetcher.fetchDiff(request, existingBranch, ctx,
                    operationsService, client, vcsRepoInfo, null, List.of("new-head"));

            assertThat(diff).isEqualTo("range diff");
        }

        @Test
        void noPr_tier1Empty_tier2Available_shouldUseDeltaDiff() throws IOException {
            BranchProcessRequest request = makeRequest("main", "new-head");
            CommitRangeContext ctx = new CommitRangeContext(List.of("new-head"), "old-head", false);
            when(operationsService.getCommitRangeDiff(client, "ws", "repo", "old-head", "new-head"))
                    .thenReturn("");  // blank → skip
            when(operationsService.getCommitRangeDiff(client, "ws", "repo", "prev-success", "new-head"))
                    .thenReturn("delta diff");

            String diff = fetcher.fetchDiff(request, existingBranch, ctx,
                    operationsService, client, vcsRepoInfo, null, List.of("new-head"));

            assertThat(diff).isEqualTo("delta diff");
        }

        @Test
        void noPr_tier1Null_tier2Null_tier2_5_shouldAggregateCommitDiffs() throws IOException {
            BranchProcessRequest request = makeRequest("main", "new-head");
            CommitRangeContext ctx = new CommitRangeContext(List.of("c1", "c2"), "old-head", false);

            // Tier 1: null diffBase would cause skip since old-head != new-head
            when(operationsService.getCommitRangeDiff(client, "ws", "repo", "old-head", "new-head"))
                    .thenThrow(new IOException("not reachable"));
            when(operationsService.getCommitRangeDiff(client, "ws", "repo", "prev-success", "new-head"))
                    .thenThrow(new IOException("not reachable"));
            when(operationsService.getCommitDiff(client, "ws", "repo", "c1"))
                    .thenReturn("diff1");
            when(operationsService.getCommitDiff(client, "ws", "repo", "c2"))
                    .thenReturn("diff2");

            String diff = fetcher.fetchDiff(request, existingBranch, ctx,
                    operationsService, client, vcsRepoInfo, null, List.of("c1", "c2"));

            assertThat(diff).contains("diff1").contains("diff2");
        }

        @Test
        void allTiersFail_shouldFallBackToSingleCommitDiff() throws IOException {
            BranchProcessRequest request = makeRequest("main", "new-head");
            // No diffBase → tier 1 skipped, same commit → tier 2 skipped, empty list → tier 2.5 skipped
            CommitRangeContext ctx = new CommitRangeContext(List.of("new-head"), null, false);
            existingBranch.setLastSuccessfulCommitHash(null);

            when(operationsService.getCommitDiff(client, "ws", "repo", "new-head"))
                    .thenReturn("last resort diff");

            String diff = fetcher.fetchDiff(request, existingBranch, ctx,
                    operationsService, client, vcsRepoInfo, null, List.of("new-head"));

            assertThat(diff).isEqualTo("last resort diff");
        }
    }
}
