package org.rostilos.codecrow.vcsclient.github.actions;

import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.*;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.vcsclient.github.dto.response.RepositorySearchResult;

import java.io.IOException;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class SearchRepositoriesActionTest {

    @Mock
    private OkHttpClient okHttpClient;

    @Mock
    private Call call;

    @Mock
    private Response response;

    @Mock
    private ResponseBody responseBody;

    private SearchRepositoriesAction action;
    private ObjectMapper objectMapper;

    @BeforeEach
    void setUp() {
        action = new SearchRepositoriesAction(okHttpClient);
        objectMapper = new ObjectMapper();
    }

    @Test
    void testGetRepositories_InstallationEndpoint_ReturnsRepositories() throws IOException {
        String jsonResponse = """
            {
                "total_count": 2,
                "repositories": [
                    {
                        "id": 1,
                        "name": "repo1",
                        "full_name": "owner/repo1",
                        "html_url": "https://github.com/owner/repo1",
                        "description": "Test repo 1",
                        "private": false,
                        "default_branch": "main"
                    },
                    {
                        "id": 2,
                        "name": "repo2",
                        "full_name": "owner/repo2",
                        "html_url": "https://github.com/owner/repo2",
                        "description": "Test repo 2",
                        "private": true,
                        "default_branch": "master"
                    }
                ]
            }
            """;

        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn(jsonResponse);
        when(response.header("Link")).thenReturn(null);

        RepositorySearchResult result = action.getRepositories("owner", 1);

        assertThat(result).isNotNull();
        assertThat(result.items()).hasSize(2);
        assertThat(result.hasNext()).isFalse();
        assertThat(result.totalCount()).isEqualTo(2);
        verify(response).close();
    }

    @Test
    void testGetRepositories_InstallationEndpointWithPagination_HasNextTrue() throws IOException {
        String jsonResponse = """
            {
                "total_count": 50,
                "repositories": [
                    {
                        "id": 1,
                        "name": "repo1",
                        "full_name": "owner/repo1",
                        "html_url": "https://github.com/owner/repo1",
                        "private": false,
                        "default_branch": "main"
                    }
                ]
            }
            """;

        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn(jsonResponse);
        when(response.header("Link")).thenReturn("<https://api.github.com/installation/repositories?page=2>; rel=\"next\"");

        RepositorySearchResult result = action.getRepositories("owner", 1);

        assertThat(result).isNotNull();
        assertThat(result.hasNext()).isTrue();
        verify(response).close();
    }

    @Test
    void testGetRepositories_InstallationEndpointFails_FallbackToUserRepos() throws IOException {
        String userReposJson = """
            [
                {
                    "id": 1,
                    "name": "repo1",
                    "full_name": "owner/repo1",
                    "html_url": "https://github.com/owner/repo1",
                    "private": false,
                    "default_branch": "main"
                }
            ]
            """;

        // First call to installation endpoint fails
        Response installationResponse = mock(Response.class);
        ResponseBody installationBody = mock(ResponseBody.class);
        when(installationResponse.isSuccessful()).thenReturn(false);
        when(installationResponse.code()).thenReturn(401);
        when(installationResponse.body()).thenReturn(installationBody);
        when(installationBody.string()).thenReturn("Unauthorized");
        
        // Second call to user endpoint succeeds
        Response userResponse = mock(Response.class);
        ResponseBody userBody = mock(ResponseBody.class);
        when(userResponse.isSuccessful()).thenReturn(true);
        when(userResponse.body()).thenReturn(userBody);
        when(userBody.string()).thenReturn(userReposJson);
        when(userResponse.header("Link")).thenReturn(null);

        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute())
            .thenReturn(installationResponse)
            .thenReturn(userResponse);

        RepositorySearchResult result = action.getRepositories("owner", 1);

        assertThat(result).isNotNull();
        assertThat(result.items()).hasSize(1);
        verify(installationResponse).close();
        verify(userResponse).close();
    }

    @Test
    void testGetOrganizationRepositories_SuccessfulResponse_ReturnsRepositories() throws IOException {
        String jsonResponse = """
            [
                {
                    "id": 1,
                    "name": "org-repo",
                    "full_name": "myorg/org-repo",
                    "html_url": "https://github.com/myorg/org-repo",
                    "private": false,
                    "default_branch": "main"
                }
            ]
            """;

        // Installation endpoint fails
        Response installationResponse = mock(Response.class);
        when(installationResponse.isSuccessful()).thenReturn(false);
        when(installationResponse.code()).thenReturn(403);
        when(installationResponse.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn("Forbidden");
        
        // Organization endpoint succeeds
        Response orgResponse = mock(Response.class);
        ResponseBody orgBody = mock(ResponseBody.class);
        when(orgResponse.isSuccessful()).thenReturn(true);
        when(orgResponse.body()).thenReturn(orgBody);
        when(orgBody.string()).thenReturn(jsonResponse);
        when(orgResponse.header("Link")).thenReturn(null);

        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute())
            .thenReturn(installationResponse)
            .thenReturn(orgResponse);

        RepositorySearchResult result = action.getOrganizationRepositories("myorg", 1);

        assertThat(result).isNotNull();
        assertThat(result.items()).hasSize(1);
        verify(installationResponse).close();
        verify(orgResponse).close();
    }

    @Test
    void testSearchRepositories_IOException_PropagatesException() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenThrow(new IOException("Network error"));

        assertThatThrownBy(() -> action.searchRepositories("owner", "test", 1))
                .isInstanceOf(IOException.class)
                .hasMessage("Network error");
    }
}
