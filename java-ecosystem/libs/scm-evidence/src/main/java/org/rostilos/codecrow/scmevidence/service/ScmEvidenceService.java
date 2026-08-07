package org.rostilos.codecrow.scmevidence.service;

import org.rostilos.codecrow.scmevidence.api.AnalysisReceiptView;
import org.rostilos.codecrow.scmevidence.api.CommitEvidenceView;
import org.rostilos.codecrow.scmevidence.api.IssueProvenance;
import org.rostilos.codecrow.scmevidence.api.PromotionPlan;
import org.rostilos.codecrow.scmevidence.model.ScmAddedLineEvidence;
import org.rostilos.codecrow.scmevidence.model.ScmAnalysisReceipt;
import org.rostilos.codecrow.scmevidence.model.ScmCommitEvidence;
import org.rostilos.codecrow.scmevidence.persistence.ScmAddedLineEvidenceRepository;
import org.rostilos.codecrow.scmevidence.persistence.ScmAnalysisReceiptRepository;
import org.rostilos.codecrow.scmevidence.persistence.ScmCommitEvidenceRepository;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.model.VcsCommit;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.io.IOException;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/** Public boundary for provider-neutral SCM evidence. */
@Service
public class ScmEvidenceService {
    private final ScmCommitEvidenceRepository evidenceRepository;
    private final ScmAnalysisReceiptRepository receiptRepository;
    private final ScmAddedLineEvidenceRepository lineRepository;
    private final ScmPromotionPlanner promotionPlanner;
    private final UnifiedDiffAddedLineParser lineParser =
            new UnifiedDiffAddedLineParser();

    public ScmEvidenceService(
            ScmCommitEvidenceRepository evidenceRepository,
            ScmAnalysisReceiptRepository receiptRepository,
            ScmAddedLineEvidenceRepository lineRepository,
            ScmPromotionPlanner promotionPlanner) {
        this.evidenceRepository = evidenceRepository;
        this.receiptRepository = receiptRepository;
        this.lineRepository = lineRepository;
        this.promotionPlanner = promotionPlanner;
    }

    @Transactional
    public List<CommitEvidenceView> capture(
            Long projectId,
            VcsClient client,
            String workspace,
            String repository,
            List<VcsCommit> commits) throws IOException {
        requireProject(projectId);
        List<CommitEvidenceView> result = new ArrayList<>();
        for (VcsCommit commit : commits == null ? List.<VcsCommit>of() : commits) {
            Optional<ScmCommitEvidence> existing = evidenceRepository
                    .findByProjectIdAndCommitHash(projectId, commit.hash());
            ScmCommitEvidence evidence;
            if (existing.isPresent()) {
                evidence = existing.get();
            } else {
                String diff = client.getCommitDiff(
                        workspace, repository, commit.hash());
                evidence = new ScmCommitEvidence(
                        projectId,
                        commit.hash(),
                        PatchIdentity.sha256(diff),
                        commit.authorName(),
                        commit.authorEmail());
                for (var line : lineParser.parse(diff)) {
                    evidence.addLine(new ScmAddedLineEvidence(
                            projectId, line.filePath(), line.lineNumber(),
                            line.lineHash()));
                }
                evidence = evidenceRepository.save(evidence);
            }
            result.add(view(evidence));
        }
        return List.copyOf(result);
    }

    @Transactional
    public void recordAnalysisReceipts(
            Long projectId,
            List<String> commitHashes,
            String sourceBranch,
            String targetBranch,
            String targetBaseRevision,
            Long analysisId,
            String analysisType) {
        requireProject(projectId);
        if (commitHashes == null || commitHashes.isEmpty()) {
            return;
        }
        String branch = requireText(targetBranch, "targetBranch");
        String type = requireText(analysisType, "analysisType");
        String contextKey = contextKey(branch, targetBaseRevision, type);
        for (ScmCommitEvidence evidence : evidenceRepository
                .findByProjectIdAndCommitHashIn(projectId, commitHashes)) {
            if (!receiptRepository
                    .existsByProjectIdAndCommitEvidenceIdAndContextKey(
                            projectId, evidence.getId(), contextKey)) {
                receiptRepository.save(new ScmAnalysisReceipt(
                        projectId, evidence, sourceBranch, branch,
                        targetBaseRevision, analysisId, type, contextKey));
            }
        }
    }

    @Transactional(readOnly = true)
    public PromotionPlan planPromotion(
            Long projectId,
            List<String> currentCommitHashes,
            String targetBranch,
            String targetBaseRevision) {
        requireProject(projectId);
        List<ScmCommitEvidence> current = evidenceRepository
                .findByProjectIdAndCommitHashIn(projectId, currentCommitHashes);
        Map<String, ScmCommitEvidence> byHash = new HashMap<>();
        current.forEach(evidence -> byHash.put(evidence.getCommitHash(), evidence));
        List<CommitEvidenceView> ordered = currentCommitHashes.stream()
                .map(hash -> byHash.containsKey(hash)
                        ? view(byHash.get(hash))
                        : new CommitEvidenceView(
                                hash, "missing:" + hash, null, null))
                .toList();
        List<String> patchIds = current.stream()
                .map(ScmCommitEvidence::getPatchId)
                .distinct()
                .toList();
        List<AnalysisReceiptView> receipts = patchIds.isEmpty()
                ? List.of()
                : receiptRepository
                        .findByProjectIdAndCommitEvidencePatchIdIn(
                                projectId, patchIds)
                        .stream()
                        .map(ScmEvidenceService::view)
                        .toList();
        return promotionPlanner.plan(
                ordered, receipts, requireText(targetBranch, "targetBranch"),
                targetBaseRevision);
    }

    @Transactional(readOnly = true)
    public Optional<IssueProvenance> resolveIssueProvenance(
            Long projectId,
            List<String> commitHashesOldestFirst,
            String filePath,
            Integer lineNumber,
            String codeSnippet) {
        requireProject(projectId);
        if (commitHashesOldestFirst == null
                || commitHashesOldestFirst.isEmpty()
                || filePath == null || codeSnippet == null
                || codeSnippet.isBlank()) {
            return Optional.empty();
        }
        List<SnippetLine> snippetLines = snippetLines(codeSnippet, lineNumber);
        List<ProvenanceCandidate> matches = new ArrayList<>();
        for (SnippetLine snippetLine : snippetLines) {
            lineRepository.findMatchingLines(
                            projectId, commitHashesOldestFirst, filePath,
                            PatchIdentity.lineSha256(snippetLine.content()))
                    .forEach(line -> matches.add(new ProvenanceCandidate(
                            line, snippetLine.expectedLine())));
        }
        Map<String, Integer> order = new HashMap<>();
        for (int i = 0; i < commitHashesOldestFirst.size(); i++) {
            order.put(commitHashesOldestFirst.get(i), i);
        }
        return matches.stream()
                .min(Comparator
                        .comparingInt((ProvenanceCandidate candidate) ->
                                -order.getOrDefault(
                                        candidate.line().getCommitEvidence()
                                                .getCommitHash(), -1))
                        .thenComparingInt(candidate -> candidate.expectedLine() == null
                                ? 0
                                : Math.abs(candidate.line().getNewLineNumber()
                                        - candidate.expectedLine())))
                .map(candidate -> new IssueProvenance(
                        candidate.line().getCommitEvidence().getCommitHash(),
                        candidate.line().getCommitEvidence().getAuthorName(),
                        candidate.line().getCommitEvidence().getAuthorEmail(),
                        candidate.line().getFilePath(),
                        candidate.line().getNewLineNumber(),
                        candidate.expectedLine() != null
                                && candidate.line().getNewLineNumber()
                                        == candidate.expectedLine()
                                ? "EXACT_LINE_AND_CONTENT"
                                : "EXACT_CONTENT"));
    }

    private static List<SnippetLine> snippetLines(
            String codeSnippet, Integer firstLineNumber) {
        String normalized = codeSnippet.replace("\r\n", "\n");
        String[] lines = normalized.split("\n", -1);
        if (lines.length == 1) {
            return List.of(new SnippetLine(lines[0], firstLineNumber));
        }
        List<SnippetLine> result = new ArrayList<>();
        for (int index = 0; index < lines.length; index++) {
            if (!lines[index].isBlank()) {
                result.add(new SnippetLine(
                        lines[index], firstLineNumber == null
                                ? null : firstLineNumber + index));
            }
        }
        return result;
    }

    private record SnippetLine(String content, Integer expectedLine) {}

    private record ProvenanceCandidate(
            ScmAddedLineEvidence line, Integer expectedLine) {}

    private static CommitEvidenceView view(ScmCommitEvidence evidence) {
        return new CommitEvidenceView(
                evidence.getCommitHash(), evidence.getPatchId(),
                evidence.getAuthorName(), evidence.getAuthorEmail());
    }

    private static AnalysisReceiptView view(ScmAnalysisReceipt receipt) {
        ScmCommitEvidence evidence = receipt.getCommitEvidence();
        return new AnalysisReceiptView(
                evidence.getCommitHash(), evidence.getPatchId(),
                receipt.getSourceBranch(), receipt.getTargetBranch(),
                receipt.getTargetBaseRevision(), receipt.getAnalysisId(),
                receipt.getAnalysisType());
    }

    private static String contextKey(
            String targetBranch, String targetBaseRevision,
            String analysisType) {
        return PatchIdentity.digest(targetBranch + "\n"
                + (targetBaseRevision == null ? "" : targetBaseRevision)
                + "\n" + analysisType);
    }

    private static void requireProject(Long projectId) {
        if (projectId == null) {
            throw new IllegalArgumentException("projectId is required");
        }
    }

    private static String requireText(String value, String field) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(field + " is required");
        }
        return value.trim();
    }
}
