package org.rostilos.codecrow.analysisengine.service.gitgraph;

import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.gitgraph.CommitAnalysisStatus;
import org.rostilos.codecrow.core.model.gitgraph.CommitNode;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.persistence.repository.gitgraph.CommitNodeRepository;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.model.VcsCommit;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.io.IOException;
import java.time.OffsetDateTime;
import java.util.*;
import java.util.stream.Collectors;

@Service
public class GitGraphSyncService {

    private static final Logger log = LoggerFactory.getLogger(GitGraphSyncService.class);

    private final CommitNodeRepository commitNodeRepository;

    public GitGraphSyncService(CommitNodeRepository commitNodeRepository) {
        this.commitNodeRepository = commitNodeRepository;
    }

    /**
     * Sync the git graph for a branch and return the full node map (hash → CommitNode).
     * This is the primary entry point for the analysis flow:
     * 1. Fetches commit history from VCS
     * 2. Creates/updates CommitNode records
     * 3. Links parent edges
     * 4. Returns the map so the caller can inspect analysisStatus per commit
     *
     * @return map of commitHash → CommitNode for all synced commits, or empty map on failure
     */
    @Transactional
    public Map<String, CommitNode> syncBranchGraph(Project project, VcsClient vcsClient, String branchName, int limit) {
        try {
            log.info("Syncing git graph for project {} branch {}", project.getId(), branchName);
            List<VcsCommit> commits = vcsClient.getCommitHistory(
                    project.getEffectiveVcsRepoInfo().getRepoWorkspace(),
                    project.getEffectiveVcsRepoInfo().getRepoSlug(),
                    branchName,
                    limit
            );

            if (commits == null || commits.isEmpty()) {
                log.warn("No commits found for branch {}", branchName);
                return Collections.emptyMap();
            }

            // 1. Fetch existing nodes
            List<String> hashes = commits.stream().map(VcsCommit::hash).collect(Collectors.toList());
            List<CommitNode> existingNodes = commitNodeRepository.findByProjectIdAndCommitHashes(project.getId(), hashes);
            Map<String, CommitNode> nodeMap = existingNodes.stream()
                    .collect(Collectors.toMap(CommitNode::getCommitHash, n -> n));

            // 2. Create missing nodes (default status = NOT_ANALYZED)
            List<CommitNode> newNodes = new ArrayList<>();
            for (VcsCommit commit : commits) {
                if (!nodeMap.containsKey(commit.hash())) {
                    CommitNode node = new CommitNode();
                    node.setProject(project);
                    node.setCommitHash(commit.hash());
                    node.setAuthorName(commit.authorName());
                    node.setAuthorEmail(commit.authorEmail());
                    node.setCommitMessage(commit.message());
                    node.setCommitTimestamp(commit.timestamp());
                    // analysisStatus defaults to NOT_ANALYZED via entity default
                    newNodes.add(node);
                    nodeMap.put(commit.hash(), node);
                }
            }

            if (!newNodes.isEmpty()) {
                commitNodeRepository.saveAll(newNodes);
            }

            // 3. Link parents
            boolean edgesUpdated = false;
            for (VcsCommit commit : commits) {
                CommitNode node = nodeMap.get(commit.hash());
                if (node != null && commit.parentHashes() != null) {
                    for (String parentHash : commit.parentHashes()) {
                        CommitNode parentNode = nodeMap.get(parentHash);
                        if (parentNode == null) {
                            // Parent might not be in the fetched limit, try to find it in DB
                            parentNode = commitNodeRepository.findByProjectIdAndCommitHash(project.getId(), parentHash).orElse(null);
                            if (parentNode != null) {
                                nodeMap.put(parentHash, parentNode);
                            }
                        }
                        if (parentNode != null && !node.getParents().contains(parentNode)) {
                            node.getParents().add(parentNode);
                            edgesUpdated = true;
                        }
                    }
                }
            }

            if (edgesUpdated) {
                commitNodeRepository.saveAll(nodeMap.values());
            }

            log.info("Git graph sync complete for project {} branch {}. Total nodes: {}, new: {}, " +
                            "analyzed: {}, not_analyzed: {}",
                    project.getId(), branchName, nodeMap.size(), newNodes.size(),
                    nodeMap.values().stream().filter(CommitNode::isAnalyzed).count(),
                    nodeMap.values().stream().filter(n -> !n.isAnalyzed()).count());

            return nodeMap;

        } catch (IOException e) {
            log.error("Failed to sync git graph for project {} branch {}", project.getId(), branchName, e);
            return Collections.emptyMap();
        }
    }

    /**
     * Resolve a (possibly short/prefix) commit hash to a full hash in the node map.
     * Bitbucket Cloud sends 12-char short hashes in some payloads (e.g. PR events),
     * while the node map keys are full 40-char hashes.
     *
     * @return the matching full hash, or the original hash if no prefix match is found
     */
    private String resolveHash(Map<String, CommitNode> nodeMap, String hash) {
        if (hash == null || nodeMap.containsKey(hash)) return hash;
        // Try prefix match for short hashes
        String match = null;
        for (String key : nodeMap.keySet()) {
            if (key.startsWith(hash) || hash.startsWith(key)) {
                if (match != null) {
                    // Ambiguous — multiple matches, return original
                    log.warn("Ambiguous short hash {} matches multiple commits", hash);
                    return hash;
                }
                match = key;
            }
        }
        if (match != null) {
            log.info("Resolved short hash {} to full hash {}", hash, match);
            return match;
        }
        return hash;
    }

    /**
     * Walk the DAG backwards from HEAD and collect the contiguous range of unanalyzed commits.
     * Stops at the first ANALYZED ancestor (that's our "known good" boundary).
     *
     * @param nodeMap    the synced node map from {@link #syncBranchGraph}
     * @param headHash   the commit hash at the tip of the branch (HEAD)
     * @return ordered list of unanalyzed commit hashes (oldest first), empty if HEAD is already analyzed,
     *         or {@code null} if HEAD commit was not found in the node map (caller should fall back to legacy)
     */
    public List<String> findUnanalyzedCommitRange(Map<String, CommitNode> nodeMap, String headHash) {
        String resolvedHash = resolveHash(nodeMap, headHash);
        CommitNode head = nodeMap.get(resolvedHash);
        if (head == null) {
            log.warn("HEAD commit {} not found in node map (even after prefix matching) — cannot determine DAG state", headHash);
            return null;
        }
        if (head.isAnalyzed()) {
            log.info("HEAD commit {} is already analyzed — nothing to do", resolvedHash);
            return Collections.emptyList();
        }

        // BFS backwards through parents, collecting unanalyzed commits.
        // Stop traversal at analyzed nodes (they form the "frontier").
        List<String> unanalyzed = new ArrayList<>();
        Set<String> visited = new HashSet<>();
        Deque<CommitNode> queue = new ArrayDeque<>();
        queue.add(head);
        visited.add(resolvedHash);

        String oldestUnanalyzedHash = null;

        while (!queue.isEmpty()) {
            CommitNode current = queue.poll();

            if (current.isAnalyzed()) {
                // This is the frontier — don't traverse further, but record it
                // as the base for the diff range
                continue;
            }

            unanalyzed.add(current.getCommitHash());
            oldestUnanalyzedHash = current.getCommitHash();

            for (CommitNode parent : current.getParents()) {
                if (parent != null && !visited.contains(parent.getCommitHash())) {
                    visited.add(parent.getCommitHash());
                    // Resolve from nodeMap first (may have richer state)
                    CommitNode resolvedParent = nodeMap.getOrDefault(parent.getCommitHash(), parent);
                    queue.add(resolvedParent);
                }
            }
        }

        // Reverse so oldest is first (natural chronological order)
        Collections.reverse(unanalyzed);
        
        log.info("Found {} unanalyzed commits in DAG walk (oldest: {}, newest: {})",
                unanalyzed.size(),
                unanalyzed.isEmpty() ? "none" : unanalyzed.get(0).substring(0, Math.min(7, unanalyzed.get(0).length())),
                unanalyzed.isEmpty() ? "none" : resolvedHash.substring(0, Math.min(7, resolvedHash.length())));

        return unanalyzed;
    }

    /**
     * Find the "base" commit for the unanalyzed range — the nearest analyzed ancestor.
     * Used to compute the diff range: base..HEAD.
     *
     * @return the commit hash of the nearest analyzed ancestor, or null if no analyzed ancestor exists
     */
    public String findAnalyzedAncestor(Map<String, CommitNode> nodeMap, String headHash) {
        String resolvedHash = resolveHash(nodeMap, headHash);
        CommitNode head = nodeMap.get(resolvedHash);
        if (head == null) return null;

        Set<String> visited = new HashSet<>();
        Deque<CommitNode> queue = new ArrayDeque<>();
        queue.add(head);
        visited.add(resolvedHash);

        while (!queue.isEmpty()) {
            CommitNode current = queue.poll();

            // Skip the head itself — we want ancestors
            if (!current.getCommitHash().equals(resolvedHash) && current.isAnalyzed()) {
                return current.getCommitHash();
            }

            for (CommitNode parent : current.getParents()) {
                if (parent != null && !visited.contains(parent.getCommitHash())) {
                    visited.add(parent.getCommitHash());
                    CommitNode resolvedParent = nodeMap.getOrDefault(parent.getCommitHash(), parent);
                    queue.add(resolvedParent);
                }
            }
        }

        return null; // No analyzed ancestor — this is likely the very first analysis
    }

    /**
     * Mark a list of commits as ANALYZED, linking them to the given CodeAnalysis.
     * Used by PR analysis.
     */
    @Transactional
    public void markCommitsAnalyzed(Long projectId, List<String> commitHashes, CodeAnalysis analysis) {
        if (commitHashes == null || commitHashes.isEmpty()) return;
        int updated = commitNodeRepository.markCommitsAnalyzed(projectId, commitHashes, analysis.getId());
        log.info("Marked {} commits as ANALYZED (projectId={}, analysisId={})", updated, projectId, analysis.getId());
    }

    /**
     * Mark a list of commits as ANALYZED without linking to a specific analysis.
     * Used by branch analysis where no CodeAnalysis record is created.
     */
    @Transactional
    public void markCommitsAnalyzed(Long projectId, List<String> commitHashes) {
        if (commitHashes == null || commitHashes.isEmpty()) return;
        int updated = commitNodeRepository.markCommitsAnalyzedWithoutAnalysis(projectId, commitHashes);
        log.info("Marked {} commits as ANALYZED (projectId={}, no linked analysis)", updated, projectId);
    }

    /**
     * Mark a list of commits as FAILED (eligible for retry).
     */
    @Transactional
    public void markCommitsFailed(Long projectId, List<String> commitHashes) {
        if (commitHashes == null || commitHashes.isEmpty()) return;
        int updated = commitNodeRepository.markCommitsFailed(projectId, commitHashes);
        log.info("Marked {} commits as FAILED (projectId={})", updated, projectId);
    }
}
