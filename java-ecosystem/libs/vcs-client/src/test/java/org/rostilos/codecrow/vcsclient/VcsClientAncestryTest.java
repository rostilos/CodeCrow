package org.rostilos.codecrow.vcsclient;

import org.junit.jupiter.api.Test;
import org.mockito.Answers;
import org.rostilos.codecrow.vcsclient.model.VcsCommit;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.doReturn;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;

class VcsClientAncestryTest {

    @Test
    void provesAncestryFromExactDescendantHistory() throws Exception {
        VcsClient client = mock(VcsClient.class, Answers.CALLS_REAL_METHODS);
        doReturn(List.of(commit("head"), commit("previous"), commit("base")))
                .when(client).getCommitHistory("workspace", "repo", "head", 256);

        assertThat(client.isCommitAncestor(
                "workspace", "repo", "previous", "head")).isTrue();
        verify(client).getCommitHistory("workspace", "repo", "head", 256);
    }

    @Test
    void returnsFalseWhenTheBoundedHistoryCannotProveAncestry() throws Exception {
        VcsClient client = mock(VcsClient.class, Answers.CALLS_REAL_METHODS);
        doReturn(List.of(commit("head"), commit("other")))
                .when(client).getCommitHistory("workspace", "repo", "head", 256);

        assertThat(client.isCommitAncestor(
                "workspace", "repo", "previous", "head")).isFalse();
        assertThat(client.isCommitAncestor(
                "workspace", "repo", null, "head")).isFalse();
    }

    private VcsCommit commit(String hash) {
        return new VcsCommit(hash, hash, null, null, null, List.of());
    }
}
