package org.rostilos.codecrow.analysisengine.delivery;

import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;

import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Objects;

/** Stable digest of the persisted analysis fields consumed by VCS reporting. */
public final class ReviewDeliveryTruth {
    private ReviewDeliveryTruth() {
    }

    public static String digest(CodeAnalysis analysis) {
        Objects.requireNonNull(analysis, "analysis");
        MessageDigest digest = sha256();
        add(digest, "review-delivery-truth-v1");
        add(digest, analysis.getExecutionId());
        add(digest, analysis.getArtifactManifestDigest());
        add(digest, analysis.getProject() == null
                ? null : analysis.getProject().getId());
        add(digest, analysis.getPrNumber());
        add(digest, analysis.getCommitHash());
        add(digest, analysis.getAnalysisType());
        add(digest, analysis.getStatus());
        add(digest, analysis.getAnalysisResult());
        add(digest, analysis.getBranchName());
        add(digest, analysis.getSourceBranchName());
        add(digest, analysis.getTaskId());
        add(digest, analysis.getTaskSummary());
        add(digest, analysis.getComment());

        List<String> issues = new ArrayList<>();
        for (CodeAnalysisIssue issue : analysis.getIssues() == null
                ? List.<CodeAnalysisIssue>of() : analysis.getIssues()) {
            MessageDigest issueDigest = sha256();
            add(issueDigest, issue.getSeverity());
            add(issueDigest, issue.getFilePath());
            add(issueDigest, issue.getLineNumber());
            add(issueDigest, issue.getEndLineNumber());
            add(issueDigest, issue.getScopeStartLine());
            add(issueDigest, issue.getIssueScope());
            add(issueDigest, issue.getTitle());
            add(issueDigest, issue.getReason());
            add(issueDigest, issue.getSuggestedFixDescription());
            add(issueDigest, issue.getSuggestedFixDiff());
            add(issueDigest, issue.getIssueCategory());
            add(issueDigest, issue.isResolved());
            add(issueDigest, issue.getResolvedDescription());
            add(issueDigest, issue.getResolvedByPr());
            add(issueDigest, issue.getResolvedCommitHash());
            add(issueDigest, issue.getResolvedAnalysisId());
            add(issueDigest, issue.getResolvedBy());
            add(issueDigest, issue.getLineHash());
            add(issueDigest, issue.getLineHashContext());
            add(issueDigest, issue.getIssueFingerprint());
            add(issueDigest, issue.getContentFingerprint());
            add(issueDigest, issue.getCodeSnippet());
            add(issueDigest, issue.getTrackedFromIssueId());
            add(issueDigest, issue.getTrackingConfidence());
            add(issueDigest, issue.getDetectionSource());
            issues.add(hex(issueDigest.digest()));
        }
        issues.sort(Comparator.naturalOrder());
        add(digest, issues.size());
        issues.forEach(value -> add(digest, value));
        return hex(digest.digest());
    }

    public static String stableId(String namespace, String... coordinates) {
        MessageDigest digest = sha256();
        add(digest, namespace);
        for (String coordinate : coordinates) {
            add(digest, coordinate);
        }
        return hex(digest.digest());
    }

    private static void add(MessageDigest digest, Object value) {
        byte[] bytes = String.valueOf(value == null ? "<null>" : value)
                .getBytes(StandardCharsets.UTF_8);
        digest.update(ByteBuffer.allocate(Integer.BYTES).putInt(bytes.length).array());
        digest.update(bytes);
    }

    private static MessageDigest sha256() {
        try {
            return MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException("SHA-256 is unavailable", impossible);
        }
    }

    private static String hex(byte[] bytes) {
        return java.util.HexFormat.of().formatHex(bytes);
    }
}
