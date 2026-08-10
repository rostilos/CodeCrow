package org.rostilos.codecrow.scmevidence.service;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.scmevidence.api.AnalysisReceiptView;
import org.rostilos.codecrow.scmevidence.api.CommitEvidenceView;
import org.rostilos.codecrow.scmevidence.api.PromotionPlan;

import java.util.ArrayList;
import java.util.List;
import java.util.stream.IntStream;

import static org.assertj.core.api.Assertions.assertThat;

class ScmPromotionPlannerTest {
    private final ScmPromotionPlanner planner = new ScmPromotionPlanner();

    @Test
    void patchIdentitySurvivesCherryPickCoordinatesButNotContentChanges() {
        String first = """
                diff --git a/src/A.java b/src/A.java
                index 1111111..2222222 100644
                --- a/src/A.java
                +++ b/src/A.java
                @@ -10,2 +10,3 @@
                 context
                +return secure(value);
                """;
        String cherryPicked = """
                diff --git a/src/A.java b/src/A.java
                index aaaaaaa..bbbbbbb 100644
                --- a/src/A.java
                +++ b/src/A.java
                @@ -410,2 +512,3 @@
                 different surrounding context
                +return secure(value);
                """;
        String changed = cherryPicked.replace("secure(value)", "unsafe(value)");

        assertThat(PatchIdentity.sha256(cherryPicked))
                .isEqualTo(PatchIdentity.sha256(first));
        assertThat(PatchIdentity.sha256(changed))
                .isNotEqualTo(PatchIdentity.sha256(first));
    }

    @Test
    void developToMasterReusesFourHundredCommitEvidenceButReviewsMasterContext() {
        List<CommitEvidenceView> commits = IntStream.range(0, 400)
                .mapToObj(index -> commit(index, "patch-" + index))
                .toList();
        // The 400 commits deliberately rotate through the 231-file fixture.
        assertThat(IntStream.range(0, 400)
                .map(index -> index % 231).distinct().count()).isEqualTo(231);
        List<AnalysisReceiptView> developReceipts = commits.stream()
                .map(commit -> receipt(commit, "feature/1-x", "develop", "dev-base"))
                .toList();

        PromotionPlan master = planner.plan(
                commits, developReceipts, "master", "master-base");

        assertThat(master.reuseKind())
                .isEqualTo(PromotionPlan.ReuseKind.EXACT_EVIDENCE_REUSE);
        assertThat(master.reusableCommits()).hasSize(400);
        assertThat(master.commitsWithoutEvidence()).isEmpty();
        assertThat(master.requiresTargetContextAnalysis()).isTrue();

        PromotionPlan exactDevelopRetry = planner.plan(
                commits, developReceipts, "develop", "dev-base");
        assertThat(exactDevelopRetry.requiresTargetContextAnalysis()).isFalse();
    }

    @Test
    void promotionWithAdditionalReleaseCommitsIsPartialAndRequiresAnalysis() {
        List<CommitEvidenceView> develop = IntStream.range(0, 12)
                .mapToObj(index -> commit(index, "patch-" + index))
                .toList();
        List<CommitEvidenceView> promoted = new ArrayList<>(develop);
        promoted.add(commit(12, "release-hotfix"));
        promoted.add(commit(13, "release-metadata"));
        List<AnalysisReceiptView> receipts = develop.stream()
                .map(commit -> receipt(commit, "feature", "develop", "d1"))
                .toList();

        PromotionPlan plan = planner.plan(
                promoted, receipts, "master", "m1");

        assertThat(plan.reuseKind())
                .isEqualTo(PromotionPlan.ReuseKind.PARTIAL_EVIDENCE_REUSE);
        assertThat(plan.reusableCommits()).hasSize(12);
        assertThat(plan.commitsWithoutEvidence())
                .containsExactly("commit-12", "commit-13");
        assertThat(plan.requiresTargetContextAnalysis()).isTrue();
    }

    private static CommitEvidenceView commit(int index, String patch) {
        return new CommitEvidenceView(
                "commit-" + index, patch, "author-" + index,
                "author-" + index + "@example.test");
    }

    private static AnalysisReceiptView receipt(
            CommitEvidenceView commit,
            String source,
            String target,
            String base) {
        return new AnalysisReceiptView(
                commit.commitHash(), commit.patchId(), source, target, base,
                100L, "PR_REVIEW");
    }
}
