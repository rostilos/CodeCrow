package org.rostilos.codecrow.core.model.rag;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.project.Project;

import static org.assertj.core.api.Assertions.assertThat;

class RagBranchIndexGenerationTest {

    @Test
    void activatesExactGenerationAndMakesItTheBranchCheckpoint() {
        RagBranchIndex branchIndex = new RagBranchIndex(
                new Project(), "develop", RagBranchIndexKind.DURABLE);
        branchIndex.requestRevision("develop-002");
        RagBranchIndexGeneration generation = new RagBranchIndexGeneration(
                branchIndex,
                "develop-002",
                "tenant-1-project-2-develop-generation-2",
                null,
                "master-100",
                "representation-digest");

        generation.activate("manifest-digest", 231, 840);
        branchIndex.activate(generation);

        assertThat(generation.getStatus()).isEqualTo(RagBranchIndexGenerationStatus.ACTIVE);
        assertThat(generation.getActivatedAt()).isNotNull();
        assertThat(branchIndex.getActiveGeneration()).isSameAs(generation);
        assertThat(branchIndex.getCommitHash()).isEqualTo("develop-002");
        assertThat(branchIndex.getDesiredCommitHash()).isEqualTo("develop-002");
        assertThat(branchIndex.getChunkCount()).isEqualTo(840);
        assertThat(branchIndex.getLifecycleStatus()).isEqualTo(RagBranchIndexLifecycleStatus.READY);
    }

    @Test
    void failedReplacementPreservesPreviouslyActiveGeneration() {
        RagBranchIndex branchIndex = new RagBranchIndex(
                new Project(), "master", RagBranchIndexKind.PRIMARY);
        RagBranchIndexGeneration active = new RagBranchIndexGeneration(
                branchIndex, "master-100", "master-generation-100", null,
                null, "representation-digest");
        active.activate("manifest-100", 400, 1200);
        branchIndex.activate(active);

        branchIndex.requestRevision("master-101");
        RagBranchIndexGeneration replacement = new RagBranchIndexGeneration(
                branchIndex, "master-101", "master-generation-101", active,
                "master-100", "representation-digest");
        replacement.fail("Qdrant unavailable");
        branchIndex.failUpdate(replacement.getErrorMessage());

        assertThat(branchIndex.getActiveGeneration()).isSameAs(active);
        assertThat(branchIndex.getCommitHash()).isEqualTo("master-100");
        assertThat(branchIndex.getDesiredCommitHash()).isEqualTo("master-101");
        assertThat(branchIndex.getLifecycleStatus()).isEqualTo(RagBranchIndexLifecycleStatus.READY);
        assertThat(branchIndex.getErrorMessage()).isEqualTo("Qdrant unavailable");
        assertThat(replacement.getStatus()).isEqualTo(RagBranchIndexGenerationStatus.FAILED);
    }

    @Test
    void durableOperationTracksAttemptsAndCompletion() {
        Project project = new Project();
        RagIndexOperation operation = new RagIndexOperation(
                project, "support/1.x", "support-9", "support-10", "operation-digest");
        RagBranchIndex branchIndex = new RagBranchIndex(project, "support/1.x", RagBranchIndexKind.DURABLE);
        RagBranchIndexGeneration generation = new RagBranchIndexGeneration(
                branchIndex, "support-10", "support-generation-10", null,
                "master-100", "representation-digest");

        operation.start();
        operation.succeed(generation);

        assertThat(operation.getAttemptCount()).isEqualTo(1);
        assertThat(operation.getStatus()).isEqualTo(RagIndexOperationStatus.SUCCEEDED);
        assertThat(operation.getGeneration()).isSameAs(generation);
        assertThat(operation.getCompletedAt()).isNotNull();
    }
}
