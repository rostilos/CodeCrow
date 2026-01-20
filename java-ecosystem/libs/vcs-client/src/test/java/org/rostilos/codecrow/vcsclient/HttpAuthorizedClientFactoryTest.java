package org.rostilos.codecrow.vcsclient;

import okhttp3.Interceptor;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;

import java.util.Arrays;
import java.util.Collections;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.*;

class HttpAuthorizedClientFactoryTest {

    private HttpAuthorizedClient mockGitHubDelegate;
    private HttpAuthorizedClient mockGitLabDelegate;
    private HttpAuthorizedClient mockBitbucketDelegate;
    private HttpAuthorizedClientFactory factory;
    private OkHttpClient mockClient;

    @BeforeEach
    void setUp() {
        mockGitHubDelegate = mock(HttpAuthorizedClient.class);
        mockGitLabDelegate = mock(HttpAuthorizedClient.class);
        mockBitbucketDelegate = mock(HttpAuthorizedClient.class);
        mockClient = mock(OkHttpClient.class);
        
        when(mockGitHubDelegate.getGitPlatform()).thenReturn(EVcsProvider.GITHUB);
        when(mockGitLabDelegate.getGitPlatform()).thenReturn(EVcsProvider.GITLAB);
        when(mockBitbucketDelegate.getGitPlatform()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);
        
        when(mockGitHubDelegate.createClient(anyString(), anyString())).thenReturn(mockClient);
        when(mockGitLabDelegate.createClient(anyString(), anyString())).thenReturn(mockClient);
        when(mockBitbucketDelegate.createClient(anyString(), anyString())).thenReturn(mockClient);
        
        factory = new HttpAuthorizedClientFactory(Arrays.asList(
                mockGitHubDelegate, mockGitLabDelegate, mockBitbucketDelegate
        ));
    }

    @Test
    void testCreateClient_GitHub_Success() {
        OkHttpClient result = factory.createClient("client-id", "client-secret", "github");

        assertThat(result).isNotNull();
        verify(mockGitHubDelegate).createClient("client-id", "client-secret");
    }

    @Test
    void testCreateClient_GitLab_Success() {
        OkHttpClient result = factory.createClient("client-id", "client-secret", "gitlab");

        assertThat(result).isNotNull();
        verify(mockGitLabDelegate).createClient("client-id", "client-secret");
    }

    @Test
    void testCreateClient_BitbucketCloud_Success() {
        OkHttpClient result = factory.createClient("client-id", "client-secret", "bitbucket-cloud");

        assertThat(result).isNotNull();
        verify(mockBitbucketDelegate).createClient("client-id", "client-secret");
    }

    @Test
    void testCreateClient_UnknownProvider_ThrowsException() {
        assertThatThrownBy(() -> factory.createClient("id", "secret", "unknown"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("Unknown VCS provider");
    }

    @Test
    void testCreateClient_NullClientId_ThrowsException() {
        assertThatThrownBy(() -> factory.createClient(null, "secret", "github"))
                .isInstanceOf(NullPointerException.class);
    }

    @Test
    void testCreateClient_EmptyClientSecret_ThrowsException() {
        assertThatThrownBy(() -> factory.createClient("id", "", "github"))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void testCreateClientWithBearerToken_Success() {
        OkHttpClient result = factory.createClientWithBearerToken("test-token");

        assertThat(result).isNotNull();
        assertThat(result.interceptors()).hasSize(1);
    }

    @Test
    void testCreateClientWithBearerToken_NullToken_ThrowsException() {
        assertThatThrownBy(() -> factory.createClientWithBearerToken(null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("Access token cannot be null or empty");
    }

    @Test
    void testCreateClientWithBearerToken_EmptyToken_ThrowsException() {
        assertThatThrownBy(() -> factory.createClientWithBearerToken(""))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("Access token cannot be null or empty");
    }

    @Test
    void testCreateGitHubClient_Success() {
        OkHttpClient result = factory.createGitHubClient("github-token");

        assertThat(result).isNotNull();
        assertThat(result.interceptors()).hasSize(1);
    }

    @Test
    void testCreateGitHubClient_NullToken_ThrowsException() {
        assertThatThrownBy(() -> factory.createGitHubClient(null))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void testCreateGitLabClient_Success() {
        OkHttpClient result = factory.createGitLabClient("gitlab-token");

        assertThat(result).isNotNull();
        assertThat(result.interceptors()).hasSize(1);
    }

    @Test
    void testCreateGitLabClient_NullToken_ThrowsException() {
        assertThatThrownBy(() -> factory.createGitLabClient(null))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void testCreateClientWithBearerToken_AddsAuthorizationHeader() throws Exception {
        OkHttpClient client = factory.createClientWithBearerToken("my-token");

        // Extract interceptor and verify it adds the Authorization header
        assertThat(client.interceptors()).hasSize(1);
        Interceptor interceptor = client.interceptors().get(0);
        
        // Create a test chain
        Interceptor.Chain mockChain = mock(Interceptor.Chain.class);
        Request originalRequest = new Request.Builder()
                .url("https://api.example.com/test")
                .build();
        when(mockChain.request()).thenReturn(originalRequest);
        when(mockChain.proceed(any(Request.class))).thenReturn(null);
        
        interceptor.intercept(mockChain);
        
        // Verify the intercepted request has the Authorization header
        verify(mockChain).proceed(argThat(request -> 
                request.header("Authorization") != null && 
                request.header("Authorization").equals("Bearer my-token")
        ));
    }
}
