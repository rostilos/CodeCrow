package org.rostilos.codecrow.analysisengine.util;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.model.VcsPullRequest;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.*;
class QaSourceDiffTest {
    @Test void usesImmutableBaseAndReviewedHeadInsteadOfMovingPrDiff() throws Exception {
        var client = mock(VcsClient.class);
        when(client.getPullRequest("ws", "repo", 7L)).thenReturn(new VcsPullRequest(7L, "Title", "Body", "feature", "main", "target", "base", "head", "open", false, "url"));
        when(client.getCommitRangeDiff("ws", "repo", "base", "head")).thenReturn("exact diff");
        assertThat(VcsDiffUtils.fetchPullRequestDiffAtRevision(client, "ws", "repo", 7L, "head")).isEqualTo("exact diff");
        verify(client, never()).getPullRequestDiff(anyString(), anyString(), anyLong());
    }
    @Test void advancedOrUnavailablePrMetadataDoesNotMixDifferentReviewSources() throws Exception {
        var client = mock(VcsClient.class);
        when(client.getPullRequest("ws", "repo", 7L)).thenReturn(new VcsPullRequest(7L, "Title", "Body", "feature", "main", "target", "base", "new-head", "open", false, "url"), null);
        assertThat(VcsDiffUtils.fetchPullRequestDiffAtRevision(client, "ws", "repo", 7L, "old-head")).isNull();
        assertThat(VcsDiffUtils.fetchPullRequestDiffAtRevision(client, "ws", "repo", 7L, "old-head")).isNull();
        verify(client, never()).getCommitRangeDiff(anyString(), anyString(), anyString(), anyString());
    }
}
