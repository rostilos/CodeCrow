package org.rostilos.codecrow.scmevidence.service;

import org.rostilos.codecrow.scmevidence.api.AnalysisReceiptView;
import org.rostilos.codecrow.scmevidence.api.CommitEvidenceView;
import org.rostilos.codecrow.scmevidence.api.PromotionPlan;
import org.springframework.stereotype.Service;

import java.util.HashSet;
import java.util.List;
import java.util.Set;

@Service
public class ScmPromotionPlanner {
    public PromotionPlan plan(
            List<CommitEvidenceView> currentCommits,
            List<AnalysisReceiptView> priorReceipts,
            String targetBranch,
            String targetBaseRevision) {
        Set<String> knownPatchIds = new HashSet<>();
        Set<String> exactContextPatchIds = new HashSet<>();
        for (AnalysisReceiptView receipt : priorReceipts) {
            knownPatchIds.add(receipt.patchId());
            if (targetBranch.equals(receipt.targetBranch())
                    && java.util.Objects.equals(
                            targetBaseRevision, receipt.targetBaseRevision())) {
                exactContextPatchIds.add(receipt.patchId());
            }
        }
        List<String> reusable = currentCommits.stream()
                .filter(commit -> knownPatchIds.contains(commit.patchId()))
                .map(CommitEvidenceView::commitHash)
                .toList();
        List<String> unseen = currentCommits.stream()
                .filter(commit -> !knownPatchIds.contains(commit.patchId()))
                .map(CommitEvidenceView::commitHash)
                .toList();
        PromotionPlan.ReuseKind kind = reusable.isEmpty()
                ? PromotionPlan.ReuseKind.NO_EVIDENCE_REUSE
                : unseen.isEmpty()
                        ? PromotionPlan.ReuseKind.EXACT_EVIDENCE_REUSE
                        : PromotionPlan.ReuseKind.PARTIAL_EVIDENCE_REUSE;
        boolean contextAnalysisRequired = currentCommits.stream()
                .anyMatch(commit -> !exactContextPatchIds.contains(commit.patchId()));
        return new PromotionPlan(kind, reusable, unseen, contextAnalysisRequired);
    }
}
