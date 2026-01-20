package org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions;

import okhttp3.*;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.dto.response.RepositorySearchResult;

import java.io.IOException;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class SearchBitbucketCloudReposActionTest {

    @Mock
    private OkHttpClient okHttpClient;

    @Mock
    private Call call;

    @Mock
    private Response response;

    @Mock
    private ResponseBody responseBody;

    private SearchBitbucketCloudReposAction action;

    @BeforeEach
    void setUp() {
        action = new SearchBitbucketCloudReposAction(okHttpClient);
    }

    @Test
    void testGetRepositories_SuccessfulResponse_ReturnsRepositories() throws IOException {
        String jsonResponse = """
            {
                "size": 2,
                "values": [
                    {
                        "slug": "repo1",
                        "uuid": "{uuid-1}",
                        "name": "Repository 1",
                        "full_name": "workspace/repo1",
                        "description": "Test repo 1",
                        "is_private": false,
                        "mainbranch": {
                            "name": "main"
                        },
                        "links": {
                            "html": {
                                "href": "https://bitbucket.org/workspace/repo1"
                            }
                        }
                    },
                    {
                        "slug": "repo2",
                        "uuid": "{uuid-2}",
                        "name": "Repository 2",
                        "full_name": "workspace/repo2",
                        "description": "Test repo 2",
                        "is_private": true,
                        "mainbranch": {
                            "name": "master"
                        },
                        "links": {
                            "html": {
                                "href": "https://bitbucket.org/workspace/repo2"
                            }
                        }
                    }
                ],
                "next": null
            }
            """;

        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn(jsonResponse);

        RepositorySearchResult result = action.getRepositories("workspace", 1);

        assertThat(result).isNotNull();
        assertThat(result.repositories()).hasSize(2);
        verify(response).close();
    }

    @Test
    void testGetRepositories_WithPagination_HasNextTrue() throws IOException {
        String jsonResponse = """
            {
                "size": 100,
                "values": [
                    {
                        "slug": "repo1",
                        "uuid": "{uuid-1}",
                        "name": "Repository 1",
                        "full_name": "workspace/repo1",
                        "description": "Repository 1 description",
                        "is_private": false,
                        "mainbranch": {
                            "name": "main"
                        },
                        "links": {
                            "html": {
                                "href": "https://bitbucket.org/workspace/repo1"
                            }
                        }
                    }
                ],
                "next": "https://api.bitbucket.org/2.0/repositories/workspace?page=2"
            }
            """;

        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn(jsonResponse);

        RepositorySearchResult result = action.getRepositories("workspace", 1);

        assertThat(result).isNotNull();
        assertThat(result.hasNext()).isTrue();
        verify(response).close();
    }

    @Test
    void testSearchRepositories_WithQuery_ReturnsFilteredRepositories() throws IOException {
        String jsonResponse = """
            {
                "size": 1,
                "values": [
                    {
                        "slug": "test-repo",
                        "uuid": "{uuid-test}",
                        "name": "Test Repository",
                        "full_name": "workspace/test-repo",
                        "description": "Test repository description",
                        "is_private": false,
                        "mainbranch": {
                            "name": "main"
                        },
                        "links": {
                            "html": {
                                "href": "https://bitbucket.org/workspace/test-repo"
                            }
                        }
                    }
                ],
                "next": null
            }
            """;

        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn(jsonResponse);

        RepositorySearchResult result = action.searchRepositories("workspace", "test", 1);

        assertThat(result).isNotNull();
        assertThat(result.repositories()).hasSize(1);
        verify(okHttpClient).newCall(argThat(request -> 
            request.url().toString().contains("q=name")
        ));
        verify(response).close();
    }

    @Test
    void testGetRepositories_UnsuccessfulResponse_ThrowsRuntimeException() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(false);
        when(response.code()).thenReturn(401);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn("Unauthorized");

        assertThatThrownBy(() -> action.getRepositories("workspace", 1))
                .isInstanceOf(RuntimeException.class)
                .hasMessageContaining("401")
                .hasMessageContaining("Unauthorized");

        verify(response).close();
    }

    @Test
    void testGetRepositories_IOException_PropagatesException() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenThrow(new IOException("Network error"));

        assertThatThrownBy(() -> action.getRepositories("workspace", 1))
                .isInstanceOf(IOException.class)
                .hasMessage("Network error");
    }
}
