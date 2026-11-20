package org.rostilos.codecrow.vcsclient;

import okhttp3.*;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.BitbucketCloudClient;
import org.rostilos.codecrow.vcsclient.github.GitHubClient;
import org.rostilos.codecrow.vcsclient.gitlab.GitLabClient;

import java.io.IOException;
import java.util.ArrayList;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

class VcsMergeBaseTest {
    @ParameterizedTest
    @ValueSource(strings = {"github", "gitlab", "bitbucket"})
    void resolvesExactCommonAncestorAndNeverTreatsMissingDataAsEmptyBase(String provider) throws Exception {
        OkHttpClient http = mock(OkHttpClient.class);
        var requests = new ArrayList<Request>();
        String body = switch (provider) {
            case "github" -> "{\"merge_base_commit\":{\"sha\":\"ancestor\"}}";
            case "gitlab" -> "{\"id\":\"ancestor\"}";
            default -> "{\"hash\":\"ancestor\"}";
        };
        when(http.newCall(any(Request.class))).thenAnswer(invocation -> {
            Request request = invocation.getArgument(0);
            requests.add(request);
            Call call = mock(Call.class);
            when(call.execute()).thenReturn(new Response.Builder().request(request).code(200).message("OK")
                    .protocol(Protocol.HTTP_1_1).body(ResponseBody.create(
                            requests.size() == 1 ? body : "{}", MediaType.get("application/json"))).build());
            return call;
        });
        VcsClient client = switch (provider) {
            case "github" -> new GitHubClient(http);
            case "gitlab" -> new GitLabClient(http, "https://gitlab.example/nested");
            default -> new BitbucketCloudClient(http);
        };
        assertThat(client.getMergeBase("owner", "repo", "target", "head")).isEqualTo("ancestor");
        String url = requests.get(0).url().toString();
        switch (provider) {
            case "github" -> assertThat(url).contains("/repos/owner/repo/compare/target...head");
            case "gitlab" -> assertThat(url).contains("gitlab.example/nested/api/v4/projects/owner%2Frepo/repository/merge_base")
                    .contains("refs[]=target", "refs[]=head");
            default -> assertThat(url).contains("/repositories/owner/repo/merge-base/target..head");
        }
        assertThat(requests.get(0).method()).isEqualTo("GET");
        assertThatThrownBy(() -> client.getMergeBase("owner", "repo", "target", "head")).isInstanceOf(IOException.class);
        assertThat(requests).hasSize(2);
    }
}
