package org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiRequestPreviousIssueDTO;
import org.rostilos.codecrow.analysisengine.execution.ExecutionInputArtifactBundle;
import org.rostilos.codecrow.core.model.project.config.ReviewApproach;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class PrEnrichmentReviewContextPreviousFindingsTest {
    private static final ObjectMapper MAPPER = new ObjectMapper();

    @Test
    void legacyConstructorAndExplicitlyEmptyListProduceTheOriginalJsonBytes() throws Exception {
        Map<String, String> taskContext = new LinkedHashMap<>();
        taskContext.put("z", "last");
        taskContext.put("a", "first");

        var legacy = new PrEnrichmentDataDto.ReviewContext(
                1,
                "Title",
                "Description",
                "alice",
                taskContext,
                "history",
                "rules",
                "feature",
                "main");
        var explicitlyEmpty = new PrEnrichmentDataDto.ReviewContext(
                1,
                "Title",
                "Description",
                "alice",
                taskContext,
                "history",
                "rules",
                "feature",
                "main",
                List.of());

        String expected = "{\"schemaVersion\":1,\"prTitle\":\"Title\","
                + "\"prDescription\":\"Description\",\"prAuthor\":\"alice\","
                + "\"taskContext\":{\"a\":\"first\",\"z\":\"last\"},"
                + "\"taskHistoryContext\":\"history\",\"projectRules\":\"rules\","
                + "\"sourceBranchName\":\"feature\",\"targetBranchName\":\"main\"}";

        assertThat(MAPPER.writeValueAsString(legacy)).isEqualTo(expected);
        assertThat(MAPPER.writeValueAsString(explicitlyEmpty)).isEqualTo(expected);
        assertThat(legacy.previousFindings()).isEmpty();
    }

    @Test
    void previousFindingsSnapshotCallerOrderAndSerializeEveryWireField() throws Exception {
        AiRequestPreviousIssueDTO first = previousFinding("previous-17", "src/A.java", 41);
        AiRequestPreviousIssueDTO second = previousFinding("previous-18", "src/B.java", 52);
        List<AiRequestPreviousIssueDTO> source = new ArrayList<>(List.of(first, second));

        var context = new PrEnrichmentDataDto.ReviewContext(
                1,
                "Title",
                null,
                null,
                Map.of(),
                "",
                "",
                "feature",
                "main",
                source);

        source.clear();

        assertThat(context.previousFindings())
                .extracting(AiRequestPreviousIssueDTO::id)
                .containsExactly("previous-17", "previous-18");
        assertThatThrownBy(() -> context.previousFindings().add(first))
                .isInstanceOf(UnsupportedOperationException.class);

        JsonNode serialized = MAPPER.readTree(MAPPER.writeValueAsString(context));
        JsonNode finding = serialized.path("previousFindings").get(0);

        assertThat(serialized.path("previousFindings").get(1).path("id").asText())
                .isEqualTo("previous-18");
        assertThat(finding.path("id").asText()).isEqualTo("previous-17");
        assertThat(finding.path("type").asText()).isEqualTo("security");
        assertThat(finding.path("severity").asText()).isEqualTo("high");
        assertThat(finding.path("title").asText()).isEqualTo("Unsafe input");
        assertThat(finding.path("reason").asText()).isEqualTo("Untrusted data reaches a sink");
        assertThat(finding.path("suggestedFixDescription").asText()).isEqualTo("Validate input");
        assertThat(finding.path("suggestedFixDiff").asText()).isEqualTo("+ validate(value)");
        assertThat(finding.path("file").asText()).isEqualTo("src/A.java");
        assertThat(finding.path("line").asInt()).isEqualTo(41);
        assertThat(finding.path("branch").asText()).isEqualTo("feature");
        assertThat(finding.path("pullRequestId").asText()).isEqualTo("23");
        assertThat(finding.path("status").asText()).isEqualTo("open");
        assertThat(finding.path("category").asText()).isEqualTo("SECURITY");
        assertThat(finding.path("prVersion").asInt()).isEqualTo(4);
        assertThat(finding.path("resolvedDescription").asText()).isEqualTo("not resolved");
        assertThat(finding.path("resolvedByCommit").asText()).isEqualTo("abc123");
        assertThat(finding.path("resolvedInAnalysisId").asLong()).isEqualTo(91L);
        assertThat(finding.path("codeSnippet").asText()).isEqualTo("sink(value);");
    }

    @Test
    void nullPreviousFindingsAreNormalizedAndOmitted() throws Exception {
        var context = new PrEnrichmentDataDto.ReviewContext(
                1, null, null, null, null, "", "", "feature", "main", null);

        assertThat(context.previousFindings()).isEmpty();
        assertThat(MAPPER.readTree(MAPPER.writeValueAsString(context)).has("previousFindings"))
                .isFalse();
    }

    @Test
    void currentSchemaBindsReviewApproachIntoTheEnrichmentDigest() throws Exception {
        var classic = currentContext(ReviewApproach.CLASSIC);
        var agentic = currentContext(ReviewApproach.AGENTIC);

        assertThat(MAPPER.readTree(MAPPER.writeValueAsString(classic))
                .path("reviewApproach").asText()).isEqualTo("CLASSIC");
        assertThat(ExecutionInputArtifactBundle.canonicalInputDigest(
                new byte[0], emptyEnrichment(classic)))
                .isNotEqualTo(ExecutionInputArtifactBundle.canonicalInputDigest(
                        new byte[0], emptyEnrichment(agentic)));
    }

    @Test
    void reviewApproachIsRequiredOnlyForTheCurrentSchema() {
        assertThatThrownBy(() -> new PrEnrichmentDataDto.ReviewContext(
                PrEnrichmentDataDto.CURRENT_REVIEW_CONTEXT_SCHEMA_VERSION,
                null, null, null, Map.of(), "", "[]", "feature", "main",
                List.of(), null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("reviewApproach");
        assertThatThrownBy(() -> new PrEnrichmentDataDto.ReviewContext(
                1, null, null, null, Map.of(), "", "[]", "feature", "main",
                List.of(), ReviewApproach.CLASSIC))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("schemaVersion 1");
    }

    private static PrEnrichmentDataDto.ReviewContext currentContext(
            ReviewApproach approach) {
        return new PrEnrichmentDataDto.ReviewContext(
                PrEnrichmentDataDto.CURRENT_REVIEW_CONTEXT_SCHEMA_VERSION,
                null,
                null,
                null,
                Map.of(),
                "",
                "[]",
                "feature",
                "main",
                List.of(),
                approach);
    }

    private static PrEnrichmentDataDto emptyEnrichment(
            PrEnrichmentDataDto.ReviewContext context) {
        return new PrEnrichmentDataDto(
                List.of(),
                List.of(),
                List.of(),
                new PrEnrichmentDataDto.EnrichmentStats(
                        0, 0, 0, 0, 0, 0, Map.of()),
                context);
    }

    private static AiRequestPreviousIssueDTO previousFinding(
            String id,
            String file,
            int line) {
        return new AiRequestPreviousIssueDTO(
                id,
                "security",
                "high",
                "Unsafe input",
                "Untrusted data reaches a sink",
                "Validate input",
                "+ validate(value)",
                file,
                line,
                "feature",
                "23",
                "open",
                "SECURITY",
                4,
                "not resolved",
                "abc123",
                91L,
                "sink(value);");
    }
}
