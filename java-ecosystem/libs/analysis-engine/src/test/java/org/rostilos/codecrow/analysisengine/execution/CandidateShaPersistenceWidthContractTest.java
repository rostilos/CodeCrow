package org.rostilos.codecrow.analysisengine.execution;

import static org.assertj.core.api.Assertions.assertThat;

import jakarta.persistence.Column;
import java.lang.reflect.Field;
import java.util.Map;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.commitgraph.model.AnalyzedCommit;
import org.rostilos.codecrow.core.model.analysis.AnalysisLock;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.pullrequest.PullRequest;
import org.rostilos.codecrow.filecontent.model.AnalyzedFileSnapshot;

class CandidateShaPersistenceWidthContractTest {

    @Test
    void everyCandidateReviewCommitCoordinateAcceptsSha256ObjectIds()
            throws Exception {
        Map<Class<?>, String> candidateWrites = Map.of(
                Job.class, "commitHash",
                AnalysisLock.class, "commitHash",
                PullRequest.class, "commitHash",
                CodeAnalysis.class, "commitHash",
                CodeAnalysisIssue.class, "resolvedCommitHash",
                AnalyzedFileSnapshot.class, "commitHash",
                AnalyzedCommit.class, "commitHash");

        for (Map.Entry<Class<?>, String> write : candidateWrites.entrySet()) {
            Field field = write.getKey().getDeclaredField(write.getValue());
            assertThat(field.getAnnotation(Column.class).length())
                    .as("%s.%s", write.getKey().getSimpleName(), write.getValue())
                    .isEqualTo(64);
        }
    }
}
