package org.rostilos.codecrow.vcsclient.gitlab.actions;

import okhttp3.*;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.vcsclient.gitlab.dto.response.RepositorySearchResult;

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

    @BeforeEach
    void setUp() {
        action = new SearchRepositoriesAction(okHttpClient);
    }

    @Test
    void testGetRepositories_WithGroupId_ReturnsRepositories() throws IOException {
        String jsonResponse = """
            [
                {
                    "id": 1,
                    "name": "project1",
                    "path": "project1",
                    "path_with_namespace": "group/project1",
                    "web_url": "https://gitlab.com/group/project1",
                    "description": "Test project 1",
                    "default_branch": "main"
                },
                {
                    "id": 2,
                    "name": "project2",
                    "path": "project2",
                    "path_with_namespace": "group/project2",
                    "web_url": "https://gitlab.com/group/project2",
                    "description": "Test project 2",
                    "default_branch": "master"
                }
            ]
            """;

        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn(jsonResponse);
        when(response.header("X-Next-Page")).thenReturn(null);

        RepositorySearchResult result = action.getRepositories("my-group", 1);

        assertThat(result).isNotNull();
        assertThat(result.items()).hasSize(2);
        assertThat(result.hasNext()).isFalse();
        verify(response).close();
    }

    @Test
    void testGetRepositories_WithoutGroupId_ReturnsUserRepositories() throws IOException {
        String jsonResponse = """
            [
                {
                    "id": 1,
                    "name": "user-project",
                    "path": "user-project",
                    "path_with_namespace": "user/user-project",
                    "web_url": "https://gitlab.com/user/user-project",
                    "default_branch": "main"
                }
            ]
            """;

        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn(jsonResponse);
        when(response.header("X-Next-Page")).thenReturn(null);

        RepositorySearchResult result = action.getRepositories(null, 1);

        assertThat(result).isNotNull();
        assertThat(result.items()).hasSize(1);
        verify(okHttpClient).newCall(argThat(request -> 
            request.url().toString().contains("membership=true")
        ));
        verify(response).close();
    }

    @Test
    void testGetRepositories_WithPagination_HasNextTrue() throws IOException {
        String jsonResponse = """
            [
                {
                    "id": 1,
                    "name": "project1",
                    "path": "project1",
                    "path_with_namespace": "group/project1",
                    "web_url": "https://gitlab.com/group/project1",
                    "default_branch": "main"
                }
            ]
            """;

        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn(jsonResponse);
        when(response.header("X-Next-Page")).thenReturn("2");

        RepositorySearchResult result = action.getRepositories("my-group", 1);

        assertThat(result).isNotNull();
        assertThat(result.hasNext()).isTrue();
        verify(response).close();
    }

    @Test
    void testGetGroupRepositories_SuccessfulResponse_ReturnsRepositories() throws IOException {
        String jsonResponse = """
            [
                {
                    "id": 1,
                    "name": "group-project",
                    "path": "group-project",
                    "path_with_namespace": "mygroup/group-project",
                    "web_url": "https://gitlab.com/mygroup/group-project",
                    "default_branch": "main"
                }
            ]
            """;

        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn(jsonResponse);
        when(response.header("X-Next-Page")).thenReturn(null);

        RepositorySearchResult result = action.getGroupRepositories("mygroup", 1);

        assertThat(result).isNotNull();
        assertThat(result.items()).hasSize(1);
        verify(okHttpClient).newCall(argThat(request -> 
            request.url().toString().contains("include_subgroups=true")
        ));
        verify(response).close();
    }

    @Test
    void testSearchRepositories_WithQuery_ReturnsFilteredRepositories() throws IOException {
        String jsonResponse = """
            [
                {
                    "id": 1,
                    "name": "test-project",
                    "path": "test-project",
                    "path_with_namespace": "group/test-project",
                    "web_url": "https://gitlab.com/group/test-project",
                    "default_branch": "main"
                }
            ]
            """;

        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn(jsonResponse);
        when(response.header("X-Next-Page")).thenReturn(null);

        RepositorySearchResult result = action.searchRepositories("my-group", "test", 1);

        assertThat(result).isNotNull();
        assertThat(result.items()).hasSize(1);
        verify(okHttpClient).newCall(argThat(request -> 
            request.url().toString().contains("search=test")
        ));
        verify(response).close();
    }

    @Test
    void testSearchRepositories_IOException_PropagatesException() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenThrow(new IOException("Network error"));

        assertThatThrownBy(() -> action.searchRepositories("my-group", "test", 1))
                .isInstanceOf(IOException.class)
                .hasMessage("Network error");
    }

}
