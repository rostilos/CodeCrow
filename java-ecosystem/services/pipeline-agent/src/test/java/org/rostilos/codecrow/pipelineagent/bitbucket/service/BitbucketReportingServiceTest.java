package org.rostilos.codecrow.pipelineagent.bitbucket.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.Interceptor;
import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Protocol;
import okhttp3.Request;
import okhttp3.Response;
import okhttp3.ResponseBody;
import okio.Buffer;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsRepoBindingRepository;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.rostilos.codecrow.vcsclient.bitbucket.model.report.AnalysisSummary;
import org.rostilos.codecrow.vcsclient.bitbucket.model.report.CodeInsightsReport;
import org.rostilos.codecrow.vcsclient.bitbucket.service.ReportGenerator;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.atomic.AtomicInteger;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatCode;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class BitbucketReportingServiceTest {
    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();

    @Mock
    private ReportGenerator reportGenerator;
    @Mock
    private VcsClientProvider vcsClientProvider;
    @Mock
    private VcsRepoBindingRepository vcsRepoBindingRepository;

    private BitbucketReportingService service;
    private CodeAnalysis analysis;
    private Project project;
    private AnalysisSummary summary;

    @BeforeEach
    void setUp() {
        service = new BitbucketReportingService(
                reportGenerator, vcsClientProvider, vcsRepoBindingRepository);

        analysis = mock(CodeAnalysis.class);
        project = mock(Project.class);
        summary = mock(AnalysisSummary.class);

        VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
        VcsConnection connection = mock(VcsConnection.class);
        when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
        when(repoInfo.getRepoWorkspace()).thenReturn("workspace");
        when(repoInfo.getRepoSlug()).thenReturn("repo");
        when(repoInfo.getVcsConnection()).thenReturn(connection);
        when(analysis.getCommitHash()).thenReturn("head-sha");

        AnalysisSummary.IssueSummary issue = issue("src/App.java", 12, "Validate the caller");
        when(summary.getIssues()).thenReturn(List.of(issue));
        when(reportGenerator.createAnalysisSummary(analysis, 77L)).thenReturn(summary);
        when(reportGenerator.createMarkdownSummary(analysis, summary)).thenReturn("summary only");
        when(reportGenerator.createCodeInsightsReport(summary, analysis)).thenReturn(
                new CodeInsightsReport(List.of(), "details", "CodeCrow", "CodeCrow", null, "FAILED"));
        when(reportGenerator.createReportAnnotations(analysis, project)).thenReturn(Set.of());
    }

    @Test
    void postsNativeInlineCommentsWithoutDetailedIssuesReply() throws IOException {
        List<CapturedRequest> requests = new ArrayList<>();
        when(vcsClientProvider.getHttpClient(org.mockito.ArgumentMatchers.any()))
                .thenReturn(capturingClient(requests, false));

        service.postAnalysisResults(analysis, project, 42L, 77L, "99");

        CapturedRequest summaryRequest = requestAt(
                requests, "PUT", "/2.0/repositories/workspace/repo/pullrequests/42/comments/99");
        assertThat(OBJECT_MAPPER.readTree(summaryRequest.body())
                .path("content").path("raw").asText())
                .contains("summary only")
                .doesNotContain("Detailed Issues");

        List<CapturedRequest> commentPosts = requests.stream()
                .filter(request -> request.method().equals("POST"))
                .filter(request -> request.path().equals(
                        "/2.0/repositories/workspace/repo/pullrequests/42/comments"))
                .toList();
        assertThat(commentPosts).hasSize(1);

        JsonNode inlinePayload = OBJECT_MAPPER.readTree(commentPosts.get(0).body());
        assertThat(inlinePayload.path("inline").path("path").asText())
                .isEqualTo("src/App.java");
        assertThat(inlinePayload.path("inline").path("to").asInt()).isEqualTo(12);
        assertThat(inlinePayload.path("content").path("raw").asText())
                .contains("🔴 **HIGH** | Security")
                .contains("**Validate the caller**")
                .contains("[codecrow-inline-issue]: #")
                .doesNotContain("<!-- codecrow-inline-issue -->");
        assertThat(inlinePayload.has("parent")).isFalse();

        assertThat(indexOf(requests, "POST",
                "/2.0/repositories/workspace/repo/pullrequests/42/comments"))
                .isLessThan(indexOf(requests, "PUT",
                        "/2.0/repositories/workspace/repo/pullrequests/42/comments/99"));

        verify(reportGenerator, never()).createDetailedIssuesMarkdown(summary, false);
    }

    @Test
    void oneRejectedAnchorDoesNotPreventOtherInlineCommentsOrReport() {
        when(summary.getIssues()).thenReturn(List.of(
                issue("src/First.java", 10, "First issue"),
                issue("src/Second.java", 20, "Second issue")
        ));

        List<CapturedRequest> requests = new ArrayList<>();
        when(vcsClientProvider.getHttpClient(org.mockito.ArgumentMatchers.any()))
                .thenReturn(capturingClient(requests, true));

        assertThatCode(() -> service.postAnalysisResults(
                analysis, project, 42L, 77L, "99"))
                .doesNotThrowAnyException();

        assertThat(requests.stream()
                .filter(request -> request.method().equals("POST"))
                .filter(request -> request.path().endsWith("/pullrequests/42/comments")))
                .hasSize(2);
        assertThat(requests).anyMatch(request ->
                request.method().equals("PUT")
                        && request.path().endsWith("/commit/head-sha/reports/org.rostilos.codecrow"));
    }

    @Test
    void removesCurrentAndLegacyGeneratedIssueCommentsBeforePublishingCurrentIssues()
            throws IOException {
        List<CapturedRequest> requests = new ArrayList<>();
        when(vcsClientProvider.getHttpClient(org.mockito.ArgumentMatchers.any()))
                .thenReturn(cleanupCapturingClient(requests));

        service.postAnalysisResults(analysis, project, 42L, 77L, "99");

        assertThat(requests).anyMatch(request ->
                request.method().equals("DELETE")
                        && request.path().endsWith("/pullrequests/42/comments/600"));
        assertThat(requests).anyMatch(request ->
                request.method().equals("DELETE")
                        && request.path().endsWith("/pullrequests/42/comments/601"));
        assertThat(requests).anyMatch(request ->
                request.method().equals("DELETE")
                        && request.path().endsWith("/pullrequests/42/comments/602"));

        List<CapturedRequest> currentCommentPosts = requests.stream()
                .filter(request -> request.method().equals("POST"))
                .filter(request -> request.path().endsWith("/pullrequests/42/comments"))
                .toList();
        assertThat(currentCommentPosts).hasSize(1);
        assertThat(OBJECT_MAPPER.readTree(currentCommentPosts.get(0).body())
                .path("inline").path("to").asInt()).isEqualTo(12);
    }

    private AnalysisSummary.IssueSummary issue(String path, int line, String title) {
        return new AnalysisSummary.IssueSummary(
                IssueSeverity.HIGH,
                "SECURITY",
                path,
                line,
                title,
                "This path accepts an untrusted caller.",
                "Check authorization before reading the resource.",
                null,
                "https://codecrow.example/issues/7",
                7L,
                "return repository.findById(id);"
        );
    }

    private OkHttpClient capturingClient(
            List<CapturedRequest> requests,
            boolean rejectFirstInlineComment
    ) {
        AtomicInteger inlineCommentPosts = new AtomicInteger();
        Interceptor interceptor = chain -> {
            Request request = chain.request();
            Buffer buffer = new Buffer();
            if (request.body() != null) {
                request.body().writeTo(buffer);
            }
            String path = request.url().encodedPath();
            String body = buffer.readUtf8();
            requests.add(new CapturedRequest(request.method(), path, body));

            boolean inlinePost = request.method().equals("POST")
                    && path.endsWith("/pullrequests/42/comments")
                    && body.contains("\"inline\"");
            boolean rejected = inlinePost
                    && rejectFirstInlineComment
                    && inlineCommentPosts.getAndIncrement() == 0;

            String responseJson;
            if (request.method().equals("GET") && path.endsWith("/pullrequests/42/comments")) {
                responseJson = "{\"values\":[],\"next\":null}";
            } else if (inlinePost && !rejected) {
                responseJson = "{\"id\":501}";
            } else if (rejected) {
                responseJson = "{\"error\":{\"message\":\"line is not in the diff\"}}";
            } else {
                responseJson = "{}";
            }

            return new Response.Builder()
                    .request(request)
                    .protocol(Protocol.HTTP_1_1)
                    .code(rejected ? 400 : 200)
                    .message(rejected ? "Bad Request" : "OK")
                    .body(ResponseBody.create(
                            responseJson, MediaType.parse("application/json")))
                    .build();
        };
        return new OkHttpClient.Builder().addInterceptor(interceptor).build();
    }

    private OkHttpClient cleanupCapturingClient(List<CapturedRequest> requests) {
        AtomicInteger commentListRequests = new AtomicInteger();
        Interceptor interceptor = chain -> {
            Request request = chain.request();
            Buffer buffer = new Buffer();
            if (request.body() != null) {
                request.body().writeTo(buffer);
            }
            String path = request.url().encodedPath();
            String body = buffer.readUtf8();
            requests.add(new CapturedRequest(request.method(), path, body));

            String responseJson = "{}";
            if (request.method().equals("GET") && path.endsWith("/pullrequests/42/comments")) {
                if (commentListRequests.getAndIncrement() == 0) {
                    responseJson = "{\"values\":[{\"id\":600,\"content\":{\"raw\":\"old [codecrow-inline-issue]: #\"}}]}";
                } else if (commentListRequests.get() == 2) {
                    responseJson = "{\"values\":[{\"id\":601,\"content\":{\"raw\":\"old <!-- codecrow-inline-issue -->\"}}]}";
                } else {
                    responseJson = "{\"values\":[{\"id\":602,\"content\":{\"raw\":\"old <!-- codecrow-issues -->\"}}]}";
                }
            } else if (request.method().equals("POST")
                    && path.endsWith("/pullrequests/42/comments")) {
                responseJson = "{\"id\":603}";
            }

            return new Response.Builder()
                    .request(request)
                    .protocol(Protocol.HTTP_1_1)
                    .code(200)
                    .message("OK")
                    .body(ResponseBody.create(
                            responseJson, MediaType.parse("application/json")))
                    .build();
        };
        return new OkHttpClient.Builder().addInterceptor(interceptor).build();
    }

    private CapturedRequest requestAt(
            List<CapturedRequest> requests,
            String method,
            String path
    ) {
        return requests.stream()
                .filter(request -> request.method().equals(method))
                .filter(request -> request.path().equals(path))
                .findFirst()
                .orElseThrow(() -> new AssertionError("Missing request to " + path));
    }

    private int indexOf(List<CapturedRequest> requests, String method, String path) {
        for (int index = 0; index < requests.size(); index++) {
            CapturedRequest request = requests.get(index);
            if (request.method().equals(method) && request.path().equals(path)) {
                return index;
            }
        }
        return -1;
    }

    private record CapturedRequest(String method, String path, String body) {
    }
}
