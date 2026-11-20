package org.rostilos.codecrow.pipelineagent.generic.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.*;
import okio.Buffer;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsRepoBindingRepository;
import org.rostilos.codecrow.pipelineagent.bitbucket.service.BitbucketReportingService;
import org.rostilos.codecrow.pipelineagent.github.service.GitHubReportingService;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.rostilos.codecrow.vcsclient.bitbucket.service.ReportGenerator;
import org.springframework.test.util.ReflectionTestUtils;
import java.io.IOException;
import java.util.*;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

class ReviewSummaryReplacementTest {
    @ParameterizedTest @ValueSource(booleans = {false, true})
    void replacesTheExistingReportAndPrunesDuplicatesAcrossPages(boolean github) {
        var f = new Fixture(github);
        f.publish("first result", null);
        f.publish("fresh result", null);
        assertThat(f.comments).containsEntry(10L, f.marker + "\nfresh result");
        assertThat(f.comments).doesNotContainKeys(20L, 30L);
        assertThat(f.comments).containsEntry(700L, "<!-- codecrow-review-checks -->\nDependency summary");
        assertThat(f.writes.stream().filter(write -> write.method.equals("POST"))).isEmpty();
        assertThat(f.writes.stream().filter(write -> write.method.equals(f.updateMethod))).hasSize(2);
        assertThat(f.readPages).contains(2);
        assertThat(f.writes.get(0).method).isEqualTo(f.updateMethod);
    }

    @ParameterizedTest @ValueSource(booleans = {false, true})
    void placeholderBecomesTheOnlyReportAfterItsUpdateSucceeds(boolean github) {
        var f = new Fixture(github); f.comments.put(99L, "Processing");
        f.publish("fresh result", "99");
        assertThat(f.comments).containsEntry(99L, f.marker + "\nfresh result").doesNotContainKeys(10L, 20L, 30L);
        assertThat(f.writes.get(0)).isEqualTo(new Write(f.updateMethod, 99L));
    }

    @ParameterizedTest @ValueSource(booleans = {false, true})
    void uncertainUpdateNeverDeletesOtherReportsOrStartsAnotherWrite(boolean github) {
        var f = new Fixture(github); f.loseResponse = true;
        assertThatThrownBy(() -> f.publish("fresh result", null)).hasRootCauseInstanceOf(IOException.class);
        assertThat(f.writes).containsExactly(new Write(f.updateMethod, 10L));
        assertThat(f.comments).containsKeys(20L, 30L);
    }

    @ParameterizedTest @ValueSource(booleans = {false, true})
    void failedLookupCannotCreateDuplicateButKnownPlaceholderCanStillBeUpdated(boolean github) {
        var f = new Fixture(github); f.failRead = true;
        assertThatThrownBy(() -> f.publish("fresh result", null)).hasRootCauseInstanceOf(IOException.class);
        assertThat(f.writes).isEmpty();
        f.publish("fresh result", "99");
        assertThat(f.writes).containsExactly(new Write(f.updateMethod, 99L));
        assertThat(f.comments).containsKeys(10L, 20L, 30L);
    }

    @ParameterizedTest @ValueSource(booleans = {false, true})
    void firstReportIsCreatedOnceAndNextPublicationUpdatesIt(boolean github) {
        var f = new Fixture(github); f.comments.clear();
        f.publish("first result", null);
        f.publish("fresh result", null);
        assertThat(f.comments).containsOnlyKeys(900L).containsEntry(900L, f.marker + "\nfresh result");
        assertThat(f.writes).containsExactly(new Write("POST", 900L), new Write(f.updateMethod, 900L));
    }

    @ParameterizedTest @ValueSource(booleans = {false, true})
    void lostCreationResponseDoesNotTriggerAnotherPost(boolean github) {
        var f = new Fixture(github); f.comments.clear(); f.loseResponse = true;
        assertThatThrownBy(() -> f.publish("first result", null)).hasRootCauseInstanceOf(IOException.class);
        assertThat(f.writes).containsExactly(new Write("POST", 900L));
    }

    private record Write(String method, Long id) {}
    private static class Fixture {
        final ObjectMapper json = new ObjectMapper();
        final Map<Long, String> comments = new LinkedHashMap<>();
        final List<Write> writes = new ArrayList<>();
        final Set<Integer> readPages = new HashSet<>();
        final boolean github;
        final String marker, updateMethod;
        final Object service;
        final VcsRepoInfo repository = mock(VcsRepoInfo.class);
        final OkHttpClient http;
        boolean loseResponse, failRead;

        Fixture(boolean github) {
            this.github = github;
            marker = github ? "<!-- codecrow-analysis-comment -->" : "[codecrow-review]: #";
            updateMethod = github ? "PATCH" : "PUT";
            comments.put(10L, github ? marker + "\nold" : "[codecrow-ai-summarize]: #\nold");
            for (long i = 100; i < 199; i++) comments.put(i, "Human discussion " + i);
            comments.put(20L, marker + "\nduplicate");
            comments.put(30L, github ? marker + "\nduplicate" : "[Codecrow AI Summarize]\nduplicate");
            comments.put(700L, "<!-- codecrow-review-checks -->\nDependency summary");
            when(repository.getRepoWorkspace()).thenReturn("owner"); when(repository.getRepoSlug()).thenReturn("repo");
            var reports = mock(ReportGenerator.class); var clients = mock(VcsClientProvider.class);
            var bindings = mock(VcsRepoBindingRepository.class);
            service = github ? new GitHubReportingService(reports, clients, bindings)
                    : new BitbucketReportingService(reports, clients, bindings);
            http = new OkHttpClient.Builder().addInterceptor(chain -> {
                Request request = chain.request(); String body = "{}";
                if (request.method().equals("GET")) {
                    if (failRead) throw new IOException("listing unavailable");
                    int page = Optional.ofNullable(request.url().queryParameter("page")).map(Integer::parseInt).orElse(1);
                    readPages.add(page);
                    var entries = comments.entrySet().stream().skip((page - 1L) * 100).limit(100).map(entry ->
                            github ? Map.of("id", entry.getKey(), "body", entry.getValue())
                                    : Map.of("id", entry.getKey(), "content", Map.of("raw", entry.getValue()))).toList();
                    if (github) body = json.writeValueAsString(entries);
                    else {
                        var result = new LinkedHashMap<String, Object>(); result.put("values", entries);
                        if (page * 100 < comments.size()) result.put("next", request.url().newBuilder().setQueryParameter("page", String.valueOf(page + 1)).build().toString());
                        body = json.writeValueAsString(result);
                    }
                } else {
                    long id = request.method().equals("POST") ? 900L : Long.parseLong(request.url().pathSegments().get(request.url().pathSize() - 1));
                    writes.add(new Write(request.method(), id));
                    if (request.method().equals("DELETE")) comments.remove(id);
                    else {
                        Buffer buffer = new Buffer(); request.body().writeTo(buffer);
                        var payload = json.readTree(buffer.readUtf8());
                        String content = github ? payload.path("body").asText() : payload.path("content").path("raw").asText();
                        // Normalize marker position for state comparisons; provider APIs retain the actual body.
                        comments.put(id, marker + "\n" + content.replace(marker, "").trim());
                        if (loseResponse) throw new IOException("response lost after accepted write");
                        body = "{\"id\":" + id + "}";
                    }
                }
                return new Response.Builder().request(request).protocol(Protocol.HTTP_1_1).code(200).message("OK")
                        .body(ResponseBody.create(body, MediaType.get("application/json"))).build();
            }).build();
        }
        void publish(String markdown, String placeholder) {
            ReflectionTestUtils.invokeMethod(service, github ? "postSummaryComment" : "postOrUpdateComment",
                    http, repository, 42L, markdown, placeholder);
        }
    }
}
