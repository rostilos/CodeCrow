package org.rostilos.codecrow.pipelineagent.github.service;

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
import org.rostilos.codecrow.vcsclient.bitbucket.service.ReportGenerator;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatCode;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class GitHubReportingServiceTest {
    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();

    @Mock
    private ReportGenerator reportGenerator;
    @Mock
    private VcsClientProvider vcsClientProvider;
    @Mock
    private VcsRepoBindingRepository vcsRepoBindingRepository;

    private GitHubReportingService service;
    private CodeAnalysis analysis;
    private Project project;
    private AnalysisSummary summary;

    @BeforeEach
    void setUp() {
        service = new GitHubReportingService(
                reportGenerator, vcsClientProvider, vcsRepoBindingRepository);

        analysis = mock(CodeAnalysis.class);
        project = mock(Project.class);
        summary = mock(AnalysisSummary.class);

        VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
        VcsConnection connection = mock(VcsConnection.class);
        when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
        when(repoInfo.getRepoWorkspace()).thenReturn("owner");
        when(repoInfo.getRepoSlug()).thenReturn("repo");
        when(repoInfo.getVcsConnection()).thenReturn(connection);
        org.mockito.Mockito.lenient().when(analysis.getCommitHash()).thenReturn("head-sha");

        AnalysisSummary.IssueSummary issue = new AnalysisSummary.IssueSummary(
                IssueSeverity.HIGH,
                "SECURITY",
                "src/App.java",
                12,
                "Validate the caller",
                "This path accepts an untrusted caller.",
                "Check authorization before reading the resource.",
                null,
                "https://codecrow.example/issues/7",
                7L,
                "return repository.findById(id);"
        );
        org.mockito.Mockito.lenient().when(summary.getIssues()).thenReturn(List.of(issue));
        org.mockito.Mockito.lenient().when(summary.getTotalUnresolvedIssues()).thenReturn(1);
        org.mockito.Mockito.lenient().when(summary.getFileIssueCount()).thenReturn(Map.of());
        org.mockito.Mockito.lenient().when(reportGenerator.createAnalysisSummary(analysis, 77L)).thenReturn(summary);
        org.mockito.Mockito.lenient().when(reportGenerator.createMarkdownSummary(analysis, summary, true)).thenReturn("summary");
        org.mockito.Mockito.lenient().when(reportGenerator.createDetailedIssuesMarkdown(summary, true)).thenReturn("details");
    }

    @Test
    void preservesAggregateCommentAndAddsSubmittedInlineReview() throws IOException {
        List<CapturedRequest> requests = new ArrayList<>();
        when(vcsClientProvider.getHttpClient(org.mockito.ArgumentMatchers.any()))
                .thenReturn(capturingClient(requests, false, false));

        service.postAnalysisResults(analysis, project, 42L, 77L, "99");

        CapturedRequest aggregate = requestAt(requests, "/repos/owner/repo/issues/comments/99");
        assertThat(aggregate.method()).isEqualTo("PATCH");
        assertThat(OBJECT_MAPPER.readTree(aggregate.body()).path("body").asText())
                .contains("summary")
                .contains("details");

        CapturedRequest review = requestAt(
                requests, "POST", "/repos/owner/repo/pulls/42/reviews");
        JsonNode reviewPayload = OBJECT_MAPPER.readTree(review.body());
        assertThat(review.method()).isEqualTo("POST");
        assertThat(reviewPayload.path("commit_id").asText()).isEqualTo("head-sha");
        assertThat(reviewPayload.path("event").asText()).isEqualTo("COMMENT");
        assertThat(reviewPayload.path("comments").size()).isEqualTo(1);
        assertThat(reviewPayload.path("comments").get(0).path("path").asText())
                .isEqualTo("src/App.java");
        assertThat(reviewPayload.path("comments").get(0).path("line").asInt()).isEqualTo(12);
        assertThat(reviewPayload.path("comments").get(0).path("side").asText())
                .isEqualTo("RIGHT");
        assertThat(reviewPayload.path("comments").get(0).path("body").asText())
                .contains("**Validate the caller**")
                .contains("<!-- codecrow-analysis-review -->");

        CapturedRequest checkRun = requestAt(requests, "/repos/owner/repo/check-runs");
        JsonNode checkRunPayload = OBJECT_MAPPER.readTree(checkRun.body());
        assertThat(checkRunPayload.path("output").has("annotations")).isFalse();
    }

    @Test
    void removesPreviousGeneratedReviewArtifactsBeforePostingReplacement() throws IOException {
        List<CapturedRequest> requests = new ArrayList<>();
        when(vcsClientProvider.getHttpClient(org.mockito.ArgumentMatchers.any()))
                .thenReturn(capturingClient(requests, false, true));

        service.postAnalysisResults(analysis, project, 42L, 77L, "99");

        int deleteIndex = indexOf(requests, "DELETE", "/repos/owner/repo/pulls/comments/321");
        int clearIndex = indexOf(requests, "PUT", "/repos/owner/repo/pulls/42/reviews/654");
        int reviewIndex = indexOf(requests, "POST", "/repos/owner/repo/pulls/42/reviews");
        assertThat(deleteIndex).isGreaterThanOrEqualTo(0);
        assertThat(clearIndex).isGreaterThan(deleteIndex);
        assertThat(reviewIndex).isGreaterThan(deleteIndex);
        assertThat(reviewIndex).isGreaterThan(clearIndex);
        assertThat(OBJECT_MAPPER.readTree(requests.get(clearIndex).body()).path("body").asText())
                .isEqualTo("<!-- codecrow-analysis-review-cleared -->");
    }

    @Test
    void reviewRejectionDoesNotBlockTheSummaryOrCheckRun() {
        List<CapturedRequest> requests = new ArrayList<>();
        when(vcsClientProvider.getHttpClient(org.mockito.ArgumentMatchers.any()))
                .thenReturn(capturingClient(requests, true, false));

        assertThatCode(() -> service.postAnalysisResults(analysis, project, 42L, 77L, "99"))
                .doesNotThrowAnyException();

        assertThat(requests).extracting(CapturedRequest::path)
                .contains(
                        "/repos/owner/repo/issues/comments/99",
                        "/repos/owner/repo/pulls/42/reviews",
                        "/repos/owner/repo/check-runs"
                );
    }

    @Test
    void askResponseUsesNativeReviewThreadReply() throws IOException {
        List<CapturedRequest> requests = new ArrayList<>();
        when(vcsClientProvider.getHttpClient(org.mockito.ArgumentMatchers.any()))
                .thenReturn(capturingClient(requests, false, false));

        service.postCommentReplyWithContext(
                project, 42L, "321", true, "Thread-aware answer", "reviewer", "Why?");

        CapturedRequest reply = requestAt(
                requests, "/repos/owner/repo/pulls/42/comments/321/replies");
        assertThat(reply.method()).isEqualTo("POST");
        assertThat(OBJECT_MAPPER.readTree(reply.body()).path("body").asText())
                .isEqualTo("Thread-aware answer");
        assertThat(requests).noneMatch(request -> request.path().equals(
                "/repos/owner/repo/issues/42/comments"));
    }

    @Test
    void askResponseOnTimelineDoesNotProbeReviewCommentEndpoint() throws IOException {
        List<CapturedRequest> requests = new ArrayList<>();
        when(vcsClientProvider.getHttpClient(org.mockito.ArgumentMatchers.any()))
                .thenReturn(capturingClient(requests, false, false));

        service.postCommentReplyWithContext(
                project, 42L, "321", false, "Timeline answer", "reviewer", "Why?");

        assertThat(requests).extracting(CapturedRequest::path)
                .containsExactly("/repos/owner/repo/issues/42/comments");
    }

    private OkHttpClient capturingClient(
            List<CapturedRequest> requests,
            boolean rejectReviews,
            boolean includePreviousReviewComment
    ) {
        Interceptor interceptor = chain -> {
            Request request = chain.request();
            Buffer buffer = new Buffer();
            if (request.body() != null) {
                request.body().writeTo(buffer);
            }
            String path = request.url().encodedPath();
            requests.add(new CapturedRequest(request.method(), path, buffer.readUtf8()));

            boolean reviewPost = request.method().equals("POST") && path.endsWith("/reviews");
            boolean reviewCommentList = request.method().equals("GET")
                    && path.endsWith("/pulls/42/comments");
            boolean reviewList = request.method().equals("GET")
                    && path.endsWith("/pulls/42/reviews");
            boolean rejected = rejectReviews && reviewPost;
            String responseJson;
            if (reviewCommentList) {
                responseJson = includePreviousReviewComment
                        ? "[{\"id\":321,\"body\":\"old <!-- codecrow-analysis-review -->\"}]"
                        : "[]";
            } else if (reviewList) {
                responseJson = includePreviousReviewComment
                        ? "[{\"id\":654,\"body\":\"## CodeCrow Review\\n\\n"
                                + "<!-- codecrow-analysis-review -->\"}]"
                        : "[]";
            } else if (reviewPost && !rejected) {
                responseJson = "{\"id\":456}";
            } else if (rejected) {
                responseJson = "{\"message\":\"Validation Failed\"}";
            } else {
                responseJson = "{}";
            }
            return new Response.Builder()
                    .request(request)
                    .protocol(Protocol.HTTP_1_1)
                    .code(rejected ? 422 : 200)
                    .message(rejected ? "Unprocessable Entity" : "OK")
                    .body(ResponseBody.create(
                            responseJson, MediaType.parse("application/json")))
                    .build();
        };
        return new OkHttpClient.Builder().addInterceptor(interceptor).build();
    }

    private CapturedRequest requestAt(List<CapturedRequest> requests, String path) {
        return requests.stream()
                .filter(request -> request.path().equals(path))
                .findFirst()
                .orElseThrow(() -> new AssertionError("Missing request to " + path));
    }

    private CapturedRequest requestAt(
            List<CapturedRequest> requests,
            String method,
            String path
    ) {
        return requests.stream()
                .filter(request -> request.method().equals(method) && request.path().equals(path))
                .findFirst()
                .orElseThrow(() -> new AssertionError(
                        "Missing " + method + " request to " + path));
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
