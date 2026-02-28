package org.rostilos.codecrow.analysisengine.service.dag;

import org.rostilos.codecrow.analysisengine.dag.DagContext;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.processor.VcsRepoInfoImpl;
import org.rostilos.codecrow.analysisengine.service.gitgraph.GitGraphSyncService;
import org.rostilos.codecrow.core.model.gitgraph.CommitNode;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.Collections;
import java.util.List;
import java.util.Map;

@Service
public class DagSyncService {
    private static final Logger log = LoggerFactory.getLogger(DagSyncService.class);

    private final VcsClientProvider vcsClientProvider;
    private final GitGraphSyncService gitGraphSyncService;

    public DagSyncService(
            VcsClientProvider vcsClientProvider,
            GitGraphSyncService gitGraphSyncService
    ) {
        this.vcsClientProvider = vcsClientProvider;
        this.gitGraphSyncService = gitGraphSyncService;
    }


    public DagContext syncDag(Project project, VcsRepoInfoImpl vcsRepoInfoImpl, BranchProcessRequest request) {
        List<String> unanalyzedCommits = Collections.emptyList();
        String dagDiffBase = null;

        try {
            Map<String, CommitNode> nodeMap = gitGraphSyncService.syncBranchGraph(
                    project, vcsClientProvider.getClient(vcsRepoInfoImpl.vcsConnection()),
                    request.getTargetBranchName(), 100);

            if (!nodeMap.isEmpty() && request.getCommitHash() != null) {
                List<String> dagResult = gitGraphSyncService.findUnanalyzedCommitRange(
                        nodeMap, request.getCommitHash());

                if (dagResult == null) {
                    log.info("DAG could not resolve HEAD commit {} — falling back to legacy analysis",
                            request.getCommitHash());
                    return new DagContext(Collections.emptyList(), null, false);
                } else if (dagResult.isEmpty()) {
                    log.info("DAG shows HEAD commit {} is already analyzed — skipping branch analysis",
                            request.getCommitHash());
                    return new DagContext(Collections.emptyList(), null, true);
                } else {
                    unanalyzedCommits = dagResult;
                }

                dagDiffBase = gitGraphSyncService.findAnalyzedAncestor(
                        nodeMap, request.getCommitHash());

                // ── First-analysis guard ─────────────────────────────────────
                // On a brand-new project (or first push to a new branch) there
                // is no analysed ancestor, so the BFS returns the ENTIRE commit
                // history as "unanalyzed".  Analysing all of that is wasteful,
                // slow, and not what the user expects.
                //
                // Limit to HEAD only so we analyse just the latest push.
                // Subsequent pushes will have an analysed ancestor and produce
                // correct incremental ranges automatically.
                if (dagDiffBase == null && unanalyzedCommits.size() > 1) {
                    log.info("First analysis for branch {} — limiting scope from {} " +
                                    "unanalyzed commits to HEAD commit only",
                            request.getTargetBranchName(), unanalyzedCommits.size());
                    unanalyzedCommits = List.of(request.getCommitHash());
                }

                log.info("DAG analysis: {} unanalyzed commits, diff base = {} (branch={})",
                        unanalyzedCommits.size(),
                        dagDiffBase != null
                                ? dagDiffBase.substring(0, Math.min(7, dagDiffBase.length()))
                                : "none (first analysis)",
                        request.getTargetBranchName());
            }
        } catch (Exception e) {
            log.warn("Git graph sync/walk failed for branch {} — falling back to legacy diff strategy: {}",
                    request.getTargetBranchName(), e.getMessage());
        }

        return new DagContext(unanalyzedCommits, dagDiffBase, false);
    }
}
