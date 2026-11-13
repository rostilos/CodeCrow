package org.rostilos.codecrow.core.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;
import java.util.Map;
import java.util.List;
import java.util.Optional;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

class PrChangeSummaryServiceTest {
    final CodeAnalysisRepository repository = mock(CodeAnalysisRepository.class);
    final ObjectMapper mapper = new ObjectMapper();
    final PrChangeSummaryService service = new PrChangeSummaryService(repository, mapper);

    CodeAnalysis analysis(long projectId, String revision, String identity) {
        Project project = mock(Project.class); when(project.getId()).thenReturn(projectId);
        var analysis = new CodeAnalysis(); analysis.setProject(project);
        analysis.setPrNumber(9L); analysis.setCommitHash(revision); analysis.setDiffFingerprint(identity);
        return analysis;
    }
    Map<String,Object> payload(long projectId, String revision) {
        return Map.of("projectId", projectId, "pullRequestId", 9L, "sourceRevision", revision,
            "summary", "Added empty input handling", "files", List.of(Map.of("path", "a.py")),
            "relationships", List.of(), "status", "complete");
    }
    @Test void attachesWithoutSeparateWriteAndLoadsExactAnalysis() {
        var first = analysis(7L, "abc", "base-one");
        var second = analysis(7L, "abc", "base-two");
        service.attach(first, payload(7L, "abc"));
        var changed = new java.util.HashMap<String,Object>(payload(7L, "abc"));
        changed.put("summary", "Different target base"); service.attach(second, changed);
        verifyNoInteractions(repository);
        when(repository.findByIdAndProjectId(101L, 7L)).thenReturn(Optional.of(first));
        when(repository.findByIdAndProjectId(102L, 7L)).thenReturn(Optional.of(second));
        assertThat(service.find(7L, 9L, "abc", 101L)).containsEntry("summary", "Added empty input handling");
        assertThat(service.find(7L, 9L, "abc", 102L)).containsEntry("summary", "Different target base");
        assertThat(service.find(8L, 9L, "abc", 101L)).isNull();
        assertThat(service.find(7L, 10L, "abc", 101L)).isNull();
        assertThat(service.find(7L, 9L, "new", 101L)).isNull();
        verify(repository, never()).findByProjectIdAndCommitHashAndPrNumber(any(), any(), any());
    }
    @Test void unknownExactAnalysisDoesNotSelectAnotherIteration() {
        assertThat(service.find(7L, 9L, "abc", 999L)).isNull();
        verify(repository, never()).findByProjectIdAndCommitHashAndPrNumber(any(), any(), any());
    }
    @Test void rejectsCrossTenantOrStalePayloadAndDifferentReviewCacheCopy() {
        var own = analysis(7L, "abc", "base-one");
        service.attach(own, payload(8L, "abc")); assertThat(own.getPrChangeSummaryPayload()).isNull();
        service.attach(own, payload(7L, "old")); assertThat(own.getPrChangeSummaryPayload()).isNull();
        service.attach(own, payload(7L, "abc"));
        for (var target : List.of(analysis(8L, "abc", "base-one"), analysis(7L, "new", "base-one"),
                analysis(7L, "abc", "base-two"))) {
            service.copyForAnalysis(own, target); assertThat(target.getPrChangeSummaryPayload()).isNull();
        }
        var same = analysis(7L, "abc", "base-one"); service.copyForAnalysis(own, same);
        assertThat(same.getPrChangeSummaryPayload()).isEqualTo(own.getPrChangeSummaryPayload());
    }
    @Test void serializationOrLookupFailureAllowsCoreReviewAndSourceFallback() throws Exception {
        var brokenMapper = mock(ObjectMapper.class);
        when(brokenMapper.writeValueAsString(any())).thenThrow(new IllegalArgumentException("malformed"));
        var analysis = analysis(7L, "abc", "identity");
        assertThatCode(() -> new PrChangeSummaryService(repository, brokenMapper).attach(analysis, payload(7L, "abc")))
                .doesNotThrowAnyException();
        assertThat(analysis.getPrChangeSummaryPayload()).isNull();
        when(repository.findByIdAndProjectId(101L, 7L)).thenThrow(new IllegalStateException("unavailable"));
        assertThat(service.find(7L, 9L, "abc", 101L)).isNull();
    }
    @Test void hostSourcesSurviveAnUnavailableSemanticScanButRemainScoped() throws Exception {
        var own = analysis(7L, "abc", "identity");
        var source = Map.of("rawDiff", "whole acquired diff", "baseRevision", "base", "targetRevision", "target");
        var payload = new java.util.HashMap<String, Object>(payload(7L, "abc"));
        payload.put("semanticStatus", "unavailable"); payload.put("toolContext", Map.of("reviewSource", source));
        service.attach(own, payload);
        assertThat(service.reviewSource(own)).isEqualTo(source);
        own.setCommitHash("new-head"); assertThat(service.reviewSource(own)).isEmpty();
        own.setCommitHash("abc"); own.setPrNumber(10L); assertThat(service.reviewSource(own)).isEmpty();
    }

    @Test void malformedSavedContextAllowsSourceFallback() throws Exception {
        var analysis = analysis(7L, "abc", "identity");
        when(repository.findByIdAndProjectId(101L, 7L)).thenReturn(Optional.of(analysis));
        for (var payload : List.of(Map.of("projectId", 7L), payload(8L, "abc"))) {
            analysis.setPrChangeSummaryPayload(mapper.writeValueAsString(payload));
            assertThat(service.find(7L, 9L, "abc", 101L)).isNull();
        }
        analysis.setPrChangeSummaryPayload("not json");
        assertThat(service.find(7L, 9L, "abc", 101L)).isNull();
    }
    @Test void incompleteSemanticScanFallsBackButGraphOnlyFailureRemainsUsable() throws Exception {
        var analysis = analysis(7L, "abc", "identity");
        when(repository.findByIdAndProjectId(101L, 7L)).thenReturn(Optional.of(analysis));
        for (String semanticStatus : List.of("degraded", "complete")) {
            var payload = new java.util.HashMap<String, Object>(payload(7L, "abc"));
            payload.put("status", "degraded"); payload.put("semanticStatus", semanticStatus);
            analysis.setPrChangeSummaryPayload(mapper.writeValueAsString(payload));
            if (semanticStatus.equals("complete")) assertThat(service.find(7L, 9L, "abc", 101L)).isNotNull();
            else assertThat(service.find(7L, 9L, "abc", 101L)).isNull();
        }
    }
}
