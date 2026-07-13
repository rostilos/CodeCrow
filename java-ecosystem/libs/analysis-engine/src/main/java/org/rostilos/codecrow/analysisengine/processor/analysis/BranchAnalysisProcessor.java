package org.rostilos.codecrow.analysisengine.processor.analysis;

import okhttp3.OkHttpClient;
import org.rostilos.codecrow.analysisengine.aiclient.AiAnalysisClient;
import org.rostilos.codecrow.commitgraph.dag.CommitRangeContext;
import org.rostilos.codecrow.analysisengine.processor.VcsRepoInfoImpl;
import org.rostilos.codecrow.commitgraph.service.BranchCommitService;
import org.rostilos.codecrow.commitgraph.service.AnalyzedCommitService;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.exception.AnalysisLockedException;
import org.rostilos.codecrow.analysisengine.service.branch.*;
import org.rostilos.codecrow.analysisengine.service.AstScopeEnricher;
import org.rostilos.codecrow.analysisengine.util.DiffParsingUtils;
import org.rostilos.codecrow.analysisengine.util.AnalysisScopeFilter;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.analysisengine.service.ProjectValidationService;
import org.rostilos.codecrow.analysisengine.service.PullRequestService;
import org.rostilos.codecrow.analysisengine.service.PullRequestStatusSyncService;
import org.rostilos.codecrow.commitgraph.service.CommitCoverageService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsAiClientService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsOperationsService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.analysisapi.rag.RagOperationsService;
import org.rostilos.codecrow.analysisengine.util.ProjectVcsInfoRetriever;
import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchRepository;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.events.EventNotificationEmitter;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.rostilos.codecrow.vcsclient.model.VcsCommit;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.util.*;
import java.util.function.Consumer;

/**
 * Orchestrates branch analysis after PR merges or direct commits.
 * <p>
 * This is a <em>thin orchestrator</em> that delegates to focused services:
 * <ul>
 * <li>{@link BranchFileOperationsService} — file-level ops (archive, snapshots,
 * branch files)</li>
 * <li>{@link BranchIssueMappingService} — CAI → BranchIssue mapping with
 * dedup</li>
 * <li>{@link BranchIssueReconciliationService} — tracking, reconciliation, AI
 * fallback</li>
 * <li>{@link DiffParsingUtils} — stateless diff parsing utilities</li>
 * </ul>
 */
@Service
public class BranchAnalysisProcessor {

	private static final Logger log = LoggerFactory.getLogger(BranchAnalysisProcessor.class);

	// ── Core dependencies ───────────────────────────────────────────────────
	private final ProjectValidationService projectService;
	private final BranchRepository branchRepository;
	private final VcsClientProvider vcsClientProvider;
	private final VcsServiceFactory vcsServiceFactory;
	private final AnalysisLockService analysisLockService;
	private final BranchAnalysisGateService branchAnalysisGateService;
	private final BranchFullReconciliationService branchFullReconciliationService;

	// ── Branch domain services ───────────────────────────────────────────
	private final BranchFileOperationsService branchFileOperationsService;
	private final BranchIssueMappingService branchIssueMappingService;
	private final BranchIssueReconciliationService branchIssueReconciliationService;
	private final BranchHealthService branchHealthService;
	private final BranchDiffFetcher branchDiffFetcher;

	// ── Commit tracking services ────────────────────────────────────────
	private final BranchCommitService branchCommitService;
	private final AnalyzedCommitService analyzedCommitService;

	// ── Hybrid (direct push) analysis dependencies ──────────────────────────
	private final CommitCoverageService commitCoverageService;
	private final CodeAnalysisService codeAnalysisService;
	private final AiAnalysisClient aiAnalysisClient;
	private final PullRequestService pullRequestService;
	private final PullRequestStatusSyncService pullRequestStatusSyncService;
	private final AstScopeEnricher astScopeEnricher;

	/**
	 * Optional RAG operations service — can be null if RAG module is not deployed.
	 */
	private final RagOperationsService ragOperationsService;

	public BranchAnalysisProcessor(
			ProjectValidationService projectService,
			BranchRepository branchRepository,
			VcsClientProvider vcsClientProvider,
			VcsServiceFactory vcsServiceFactory,
			AnalysisLockService analysisLockService,
			BranchAnalysisGateService branchAnalysisGateService,
			BranchFullReconciliationService branchFullReconciliationService,
			BranchFileOperationsService branchFileOperationsService,
			BranchIssueMappingService branchIssueMappingService,
			BranchIssueReconciliationService branchIssueReconciliationService,
			BranchHealthService branchHealthService,
			BranchDiffFetcher branchDiffFetcher,
			BranchCommitService branchCommitService,
			AnalyzedCommitService analyzedCommitService,
			CommitCoverageService commitCoverageService,
			CodeAnalysisService codeAnalysisService,
			AiAnalysisClient aiAnalysisClient,
			PullRequestService pullRequestService,
			PullRequestStatusSyncService pullRequestStatusSyncService,
			AstScopeEnricher astScopeEnricher,
			@Autowired(required = false) RagOperationsService ragOperationsService) {
		this.projectService = projectService;
		this.branchRepository = branchRepository;
		this.vcsClientProvider = vcsClientProvider;
		this.vcsServiceFactory = vcsServiceFactory;
		this.analysisLockService = analysisLockService;
		this.branchAnalysisGateService = branchAnalysisGateService;
		this.branchFullReconciliationService = branchFullReconciliationService;
		this.branchFileOperationsService = branchFileOperationsService;
		this.branchIssueMappingService = branchIssueMappingService;
		this.branchIssueReconciliationService = branchIssueReconciliationService;
		this.branchHealthService = branchHealthService;
		this.branchDiffFetcher = branchDiffFetcher;
		this.branchCommitService = branchCommitService;
		this.analyzedCommitService = analyzedCommitService;
		this.commitCoverageService = commitCoverageService;
		this.codeAnalysisService = codeAnalysisService;
		this.aiAnalysisClient = aiAnalysisClient;
		this.pullRequestService = pullRequestService;
		this.pullRequestStatusSyncService = pullRequestStatusSyncService;
		this.astScopeEnricher = astScopeEnricher;
		this.ragOperationsService = ragOperationsService;
	}

	/**
	 * Primary entry point — run branch analysis for a given request.
	 *
	 * @param request  the branch analysis request
	 * @param consumer SSE event consumer for progress updates
	 * @return result map with status/metadata
	 */
	public Map<String, Object> process(
			BranchProcessRequest request,
			Consumer<Map<String, Object>> consumer) throws IOException {
		Project project = projectService.getProjectWithConnections(request.getProjectId());

		// PR jobs are registered before async processing starts and remain active
		// until their source-branch lock is released and analysis is persisted.
		branchAnalysisGateService.awaitPrAnalyses(
				project.getId(), request.getTargetBranchName(), consumer);
		refreshMergedBranchHead(project, request);

		Optional<String> lockKey = analysisLockService.acquireLockWithWait(
				project, request.getTargetBranchName(), AnalysisLockType.BRANCH_ANALYSIS,
				request.getCommitHash(), null, consumer);

		if (lockKey.isEmpty()) {
			log.warn("Branch analysis already in progress for project={}, branch={}",
					project.getId(), request.getTargetBranchName());
			throw new AnalysisLockedException(
					AnalysisLockType.BRANCH_ANALYSIS.name(),
					request.getTargetBranchName(), project.getId());
		}

		List<String> unanalyzedCommits = Collections.emptyList();

		try {
			Optional<Branch> existingBranchOpt = branchRepository.findByProjectIdAndBranchName(
					project.getId(), request.getTargetBranchName());

			if (request.getSourcePrNumber() == null
					&& matchCache(request, existingBranchOpt, project, consumer)) {
				return Map.of(
						"status", "skipped",
						"reason", "commit_already_analyzed",
						"branch", request.getTargetBranchName(),
						"commitHash", request.getCommitHash());
			}

			EventNotificationEmitter.emitStatus(consumer, "started",
					"Branch analysis started for branch: " + request.getTargetBranchName());

			VcsRepoInfoImpl vcsRepoInfoImpl = ProjectVcsInfoRetriever.getVcsInfo(project);
			OkHttpClient client = vcsClientProvider.getHttpClient(vcsRepoInfoImpl.vcsConnection());
			EVcsProvider provider = ProjectVcsInfoRetriever.getVcsProvider(project);
			VcsOperationsService operationsService = vcsServiceFactory.getOperationsService(provider);

			// ── Commit range resolution ───────────────────────────────────
			CommitRangeContext rangeCtx = branchCommitService.resolveCommitRange(project,
					vcsRepoInfoImpl.vcsConnection(),
					request.getTargetBranchName(),
					request.getCommitHash());
			unanalyzedCommits = rangeCtx.getUnanalyzedCommits();

			EventNotificationEmitter.emitStatus(consumer, "fetching_diff", "Fetching diff for analysis");

			// ── PR number resolution ─────────────────────────────────────────
			Long prNumber = resolvePrNumber(request, operationsService, client, vcsRepoInfoImpl);
			List<String> prLookupCommitCandidates = new ArrayList<>();
			if (request.getCommitHash() != null && !request.getCommitHash().isBlank()) {
				prLookupCommitCandidates.add(request.getCommitHash());
			}

			// ── Merge-commit detection (safety net for webhook race condition) ──
			// If prNumber is still null, the PR number may have been lost because
			// repo:push arrived before pullrequest:fulfilled. Detect merge commits
			// by checking parent count: merge commits always have >1 parent.
			boolean isMergeCommit = false;
			if (prNumber == null && request.getCommitHash() != null) {
				try {
					VcsClient vcsClient = vcsClientProvider
							.getClient(vcsRepoInfoImpl.vcsConnection());
					List<VcsCommit> headCommits = vcsClient.getCommitHistory(
							vcsRepoInfoImpl.workspace(), vcsRepoInfoImpl.repoSlug(),
							request.getCommitHash(), 1);
					if (!headCommits.isEmpty()
							&& headCommits.get(0).parentHashes() != null
							&& headCommits.get(0).parentHashes().size() > 1) {
						isMergeCommit = true;
						log.info("Detected merge commit {} (parents: {}) — attempting PR lookup via parent",
								request.getCommitHash().substring(0,
										Math.min(7, request.getCommitHash()
												.length())),
								headCommits.get(0).parentHashes().size());
						// Second parent of a merge commit is the source branch HEAD.
						// Try finding the PR via this parent, which is the commit the PR was
						// based on.
						String sourceParent = headCommits.get(0).parentHashes().get(1);
						prLookupCommitCandidates.add(sourceParent);
						try {
							prNumber = operationsService.findPullRequestForCommit(
									client, vcsRepoInfoImpl.workspace(),
									vcsRepoInfoImpl.repoSlug(), sourceParent);
							if (isValidPrNumber(prNumber)) {
								log.info("Found PR #{} from merge commit's second parent {}",
										prNumber,
										sourceParent.substring(0, Math.min(7,
												sourceParent.length())));
								request.sourcePrNumber = prNumber;
							} else {
								prNumber = null;
							}
						} catch (Exception e) {
							log.debug("Could not find PR from merge parent {}: {}",
									sourceParent, e.getMessage());
						}
					}
				} catch (Exception e) {
					log.debug("Could not detect merge commit (non-critical): {}", e.getMessage());
				}
			}

			if (prNumber == null) {
				prNumber = resolvePrNumberFromReviewedPr(project, request, prLookupCommitCandidates);
			}

			if (prNumber != null) {
				EventNotificationEmitter.emitStatus(consumer, "pr_merge_context",
						"Branch analysis linked to merged PR #" + prNumber);
			}

			// Mark the source PR as MERGED if this branch analysis was triggered by a PR
			// merge.
			// This keeps PullRequestState accurate for commit coverage checks.
			Set<Long> mergedPrNumbers = new LinkedHashSet<>();
			if (prNumber != null) {
				try {
					pullRequestService.markPullRequestMerged(project.getId(), prNumber);
					mergedPrNumbers.add(prNumber);
				} catch (Exception e) {
					log.debug("Could not mark PR #{} as merged (may not exist yet): {}", prNumber,
							e.getMessage());
				}
			}

			if (prNumber != null || isMergeCommit) {
				PullRequestStatusSyncService.SyncResult syncResult = pullRequestStatusSyncService
						.syncOpenPullRequestStates(
								project, request.getTargetBranchName(), consumer);
				if (syncResult != null) {
					mergedPrNumbers.addAll(syncResult.mergedPrNumbers());
				}
			}

			// PR commits are expected to be marked analyzed by the PR processor. That
			// must not skip branch issue import after merge. Only a genuine non-merge
			// event can take the all-commits-covered fast path.
			if (rangeCtx.getSkipAnalysis()
					&& prNumber == null
					&& !isMergeCommit
					&& mergedPrNumbers.isEmpty()) {
				return skipAlreadyAnalyzedRange(project, request, existingBranchOpt, consumer);
			}

			// ── Multi-tier diff strategy ─────────────────────────────────────
			Long diffPrNumber = mergedPrNumbers.size() > 1 ? null : prNumber;
			String repositoryDiff = branchDiffFetcher.fetchDiff(request, existingBranchOpt.orElse(null), rangeCtx,
					operationsService, client, vcsRepoInfoImpl, diffPrNumber, unanalyzedCommits);
			String rawDiff = AnalysisScopeFilter.filterDiff(repositoryDiff, project);

			Set<String> changedFiles = DiffParsingUtils.parseFilePathsFromDiff(rawDiff);
			for (DiffParsingUtils.FileChange change : DiffParsingUtils.parseFileChanges(rawDiff)) {
				if ((change.changeType() == DiffParsingUtils.ChangeType.DELETED
						|| change.changeType() == DiffParsingUtils.ChangeType.RENAMED)
						&& change.oldPath() != null) {
					changedFiles.add(change.oldPath());
				}
				if (change.changeType() != DiffParsingUtils.ChangeType.DELETED
						&& change.newPath() != null) {
					changedFiles.add(change.newPath());
				}
			}
			augmentChangedFilesFromPrs(changedFiles, project, mergedPrNumbers);
			AnalysisScopeFilter.retainIncluded(changedFiles, project);

			if (changedFiles.isEmpty()) {
				branchFileOperationsService.createOrUpdateProjectBranch(
						project, request, existingBranchOpt.orElse(null));
				EventNotificationEmitter.emitStatus(consumer, "skipped",
						"No changed files match the project analysis scope");
				performIncrementalRagUpdate(request, project, repositoryDiff, consumer);
				branchHealthService.markBranchHealthy(project, request);
				branchHealthService.recordCommitsAnalyzed(project, unanalyzedCommits,
						request.getTargetBranchName());
				return Map.of("status", "accepted", "cached", false,
						"scopeFiltered", true, "branch", request.getTargetBranchName());
			}

			// Detect first-ever analysis for this branch (no prior successful commit)
			boolean isFirstAnalysis = existingBranchOpt.isEmpty()
					|| existingBranchOpt.get().getLastSuccessfulCommitHash() == null;

			// ── Hybrid analysis: AI analysis for uncovered direct pushes ─────
			// Skip on first analysis — the existing codebase predates CodeCrow
			// and should not be treated as "uncovered direct pushes".
			if (!isFirstAnalysis) {
				performDirectPushAnalysisIfNeeded(
						project, request, unanalyzedCommits, rawDiff,
						changedFiles, provider, consumer, prNumber, isMergeCommit);
			} else {
				log.info(
						"First analysis for branch {} — skipping direct push analysis (establishing baseline, {} files)",
						request.getTargetBranchName(), changedFiles.size());
			}

			EventNotificationEmitter.emitStatus(consumer, "analyzing_files",
					"Analyzing " + changedFiles.size() + " changed files");

			Map<String, String> archiveContents = branchFileOperationsService.downloadBranchArchive(
					vcsRepoInfoImpl, request.getCommitHash(), changedFiles);
			log.info("Branch archive: {} files extracted for {} changed files",
					archiveContents.size(), changedFiles.size());

			Set<String> existingFiles = branchFileOperationsService.updateBranchFiles(
					changedFiles, project, request.getTargetBranchName(), archiveContents);

			Branch branch = branchFileOperationsService.createOrUpdateProjectBranch(
					project, request, existingBranchOpt.orElse(null));

			if (mergedPrNumbers.size() > 1) {
				branchIssueMappingService.mapCodeAnalysisIssuesToBranch(
						changedFiles, existingFiles, branch, project, mergedPrNumbers);
			} else {
				branchIssueMappingService.mapCodeAnalysisIssuesToBranch(
						changedFiles, existingFiles, branch, project, prNumber);
			}
			branchIssueReconciliationService.reconcileIssueLineNumbers(rawDiff, changedFiles, branch);

			// Update branch issue counts after mapping
			Branch refreshedBranch = refreshAndSaveIssueCounts(branch);
			log.info("Updated branch issue counts after mapping: total={}, high={}, medium={}, low={}, resolved={}",
					refreshedBranch.getTotalIssues(), refreshedBranch.getHighSeverityCount(),
					refreshedBranch.getMediumSeverityCount(), refreshedBranch.getLowSeverityCount(),
					refreshedBranch.getResolvedCount());

			branchIssueReconciliationService.reanalyzeCandidateIssues(
					changedFiles, existingFiles, refreshedBranch, project,
					request, consumer, archiveContents, rawDiff);

			// ── Deterministic sweep: catch stale issues in non-diff files ────
			// The normal reconciliation above only checks files in the diff.
			// The sweep checks ALL remaining unresolved issues that have reliable
			// content anchors (codeSnippet/lineHash). Zero AI cost.
			int sweptCount = branchIssueReconciliationService.sweepDeterministicResolutions(
					changedFiles, refreshedBranch, project, request, archiveContents);
			if (sweptCount > 0) {
				refreshedBranch = refreshAndSaveIssueCounts(refreshedBranch);
			}

			branchFileOperationsService.updateFileSnapshotsForBranch(existingFiles, project, request,
					archiveContents);

			Branch branchForVerify = branchRepository.findByProjectIdAndBranchName(
					project.getId(), request.getTargetBranchName()).orElse(refreshedBranch);
			branchIssueReconciliationService.verifyIssueLineNumbersWithSnippets(
					changedFiles, project, branchForVerify);

			// ── Post-analysis housekeeping ────────────────────────────────────
			performIncrementalRagUpdate(request, project, repositoryDiff, consumer);
			branchHealthService.markBranchHealthy(project, request);
			branchHealthService.recordCommitsAnalyzed(project, unanalyzedCommits,
					request.getTargetBranchName());

			log.info("Reconciliation finished (Branch: {}, Commit: {}, status: HEALTHY)",
					request.getTargetBranchName(), request.getCommitHash());

			return Map.of("status", "accepted", "cached", false,
					"branch", request.getTargetBranchName());

		} catch (Exception e) {
			branchHealthService.handleProcessFailure(project, request, unanalyzedCommits, e);
			throw e;
		} finally {
			analysisLockService.releaseLock(lockKey.get());
		}
	}

	public Map<String, Object> fullReconcile(Long projectId, String branchName,
			Consumer<Map<String, Object>> consumer) throws IOException {
		return branchFullReconciliationService.fullReconcile(projectId, branchName, consumer);
	}

	/**
	 * Check if the incoming commit was already SUCCESSFULLY analyzed.
	 * Uses lastSuccessfulCommitHash so that failed attempts are re-processed.
	 */
	private boolean matchCache(BranchProcessRequest request, Optional<Branch> existingBranchOpt,
			Project project, Consumer<Map<String, Object>> consumer) {
		if (request.getCommitHash() == null || existingBranchOpt.isEmpty())
			return false;

		String lastSuccess = existingBranchOpt.get().getLastSuccessfulCommitHash();
		if (!request.getCommitHash().equals(lastSuccess))
			return false;

		log.info("Skipping branch analysis - commit {} already successfully analyzed for branch {} (project={})",
				request.getCommitHash(), request.getTargetBranchName(), project.getId());

		// Refresh file snapshots even on skip path
		try {
			VcsRepoInfoImpl vcsRepoInfoImpl = ProjectVcsInfoRetriever.getVcsInfo(project);
			Set<String> branchFiles = branchFileOperationsService.getBranchFilePaths(
					project.getId(), request.getTargetBranchName());
			AnalysisScopeFilter.retainIncluded(branchFiles, project);

			if (!branchFiles.isEmpty()) {
				Map<String, String> archiveContents = branchFileOperationsService.downloadBranchArchive(
						vcsRepoInfoImpl, request.getCommitHash(), branchFiles);
				branchFileOperationsService.updateFileSnapshotsForBranch(
						branchFiles, project, request, archiveContents);
			}
		} catch (Exception snapEx) {
			log.warn("Failed to refresh file snapshots on skip path (non-critical): {}",
					snapEx.getMessage());
		}

		EventNotificationEmitter.emitStatus(consumer, "skipped",
				"Commit already successfully analyzed for this branch");
		return true;
	}

	/**
	 * If sourcePrNumber is not set, try to look it up from the commit.
	 * This handles cases where branch analysis is triggered by push events.
	 */
	private Long resolvePrNumber(BranchProcessRequest request,
			VcsOperationsService operationsService,
			OkHttpClient client, VcsRepoInfoImpl vcsRepoInfoImpl) {
		Long prNumber = request.getSourcePrNumber();
		if (!isValidPrNumber(prNumber)) {
			prNumber = null;
		}
		if (prNumber == null && request.getCommitHash() != null) {
			try {
				prNumber = operationsService.findPullRequestForCommit(
						client, vcsRepoInfoImpl.workspace(), vcsRepoInfoImpl.repoSlug(),
						request.getCommitHash());
				if (isValidPrNumber(prNumber)) {
					log.info("Found PR #{} for commit {} via API lookup", prNumber,
							request.getCommitHash());
					request.sourcePrNumber = prNumber;
				} else {
					prNumber = null;
				}
			} catch (Exception e) {
				log.debug("Could not look up PR for commit {}: {}",
						request.getCommitHash(), e.getMessage());
			}
		}
		return prNumber;
	}

	/**
	 * Fallback for branch events that lost sourcePrNumber.
	 * <p>
	 * If the branch HEAD or merge-parent commit was already reviewed as a PR,
	 * recover that PR number from local analysis history so unresolved PR issues
	 * can still be mapped onto the branch after merge.
	 */
	private Long resolvePrNumberFromReviewedPr(Project project,
			BranchProcessRequest request,
			List<String> commitCandidates) {
		if (commitCandidates == null || commitCandidates.isEmpty()) {
			return null;
		}

		Set<String> uniqueCandidates = new LinkedHashSet<>(commitCandidates);
		for (String commitHash : uniqueCandidates) {
			if (commitHash == null || commitHash.isBlank()) {
				continue;
			}
			try {
				Optional<Long> prNumber = codeAnalysisService.findReviewedPrNumberByCommitHash(
						project.getId(), request.getTargetBranchName(), commitHash);
				if (prNumber.isPresent()) {
					request.sourcePrNumber = prNumber.get();
					log.info("Resolved PR #{} from local reviewed PR analysis for commit {} (branch={})",
							prNumber.get(), shortHash(commitHash),
							request.getTargetBranchName());
					return prNumber.get();
				}
			} catch (Exception e) {
				log.debug("Could not resolve PR number from local analysis for commit {}: {}",
						shortHash(commitHash), e.getMessage());
			}
		}
		return null;
	}

	private Map<String, Object> skipAlreadyAnalyzedRange(
			Project project,
			BranchProcessRequest request,
			Optional<Branch> existingBranchOpt,
			Consumer<Map<String, Object>> consumer) {
		branchFileOperationsService.createOrUpdateProjectBranch(
				project, request, existingBranchOpt.orElse(null));

		branchRepository.findByProjectIdAndBranchName(
				project.getId(), request.getTargetBranchName())
				.ifPresent(branch -> {
					branch.setLastKnownHeadCommit(request.getCommitHash());
					branchRepository.save(branch);
					log.info("Advanced lastKnownHeadCommit to {} on skip path (branch={})",
							request.getCommitHash(), request.getTargetBranchName());
				});

		EventNotificationEmitter.emitStatus(consumer, "skipped", "All commits already analyzed");
		return Map.of(
				"status", "skipped",
				"reason", "already_analyzed",
				"branch", request.getTargetBranchName(),
				"commitHash", request.getCommitHash());
	}

	/**
	 * When branch analysis is triggered by a PR merge, augment the changed-files
	 * set with file paths from the merged PR's analysis. This ensures
	 * mapCodeAnalysisIssuesToBranch picks up issues that the diff didn't cover
	 * (e.g. fast-forward merges, condensed diffs).
	 */
	private void augmentChangedFilesFromPrs(
			Set<String> changedFiles,
			Project project,
			Set<Long> prNumbers) {
		if (prNumbers == null || prNumbers.isEmpty())
			return;
		try {
			Set<String> prFilePaths = prNumbers.size() == 1
					? branchIssueMappingService.findPrIssuePaths(
							project.getId(), prNumbers.iterator().next())
					: branchIssueMappingService.findPrIssuePaths(project.getId(), prNumbers);
			int added = 0;
			for (String fp : prFilePaths) {
				if (changedFiles.add(fp))
					added++;
			}
			if (added > 0) {
				log.info("Augmented changedFiles with {} additional paths from merged PRs {} (total now: {})",
						added, prNumbers, changedFiles.size());
			}
		} catch (Exception e) {
			log.warn("Failed to augment changedFiles from merged PRs {} (non-critical): {}",
					prNumbers, e.getMessage());
		}
	}

	private void refreshMergedBranchHead(Project project, BranchProcessRequest request) {
		if (request.getSourcePrNumber() == null) {
			return;
		}
		try {
			VcsRepoInfoImpl vcsInfo = ProjectVcsInfoRetriever.getVcsInfo(project);
			VcsClient vcsClient = vcsClientProvider.getClient(vcsInfo.vcsConnection());
			String latestHead = vcsClient.getLatestCommitHash(
					vcsInfo.workspace(), vcsInfo.repoSlug(), request.getTargetBranchName());
			if (latestHead != null && !latestHead.isBlank()
					&& !latestHead.equals(request.getCommitHash())) {
				log.info("Coalesced branch {} from webhook commit {} to latest head {}",
						request.getTargetBranchName(), shortHash(request.getCommitHash()), shortHash(latestHead));
				request.commitHash = latestHead;
			}
		} catch (Exception e) {
			log.warn("Could not refresh latest head for merged branch {} — using webhook commit {}: {}",
					request.getTargetBranchName(), shortHash(request.getCommitHash()), e.getMessage());
		}
	}

	// ── Hybrid analysis: direct push AI analysis ──────────────────────────
	/**
	 * Performs AI analysis on uncovered direct pushes (commits not in any open PR).
	 * <p>
	 * This is the core of the hybrid branch analysis flow:
	 * <ol>
	 * <li>Check if unanalyzed commits are covered by open PRs targeting this
	 * branch</li>
	 * <li>If fully covered → skip (PR analysis will handle them)</li>
	 * <li>If not covered or partially covered → build AI request from commit range
	 * diff,
	 * call inference orchestrator, save resulting CodeAnalysis with
	 * {@code DetectionSource.DIRECT_PUSH_ANALYSIS}, then map issues to branch</li>
	 * </ol>
	 */
	private void performDirectPushAnalysisIfNeeded(
			Project project,
			BranchProcessRequest request,
			List<String> unanalyzedCommits,
			String rawDiff,
			Set<String> changedFiles,
			EVcsProvider provider,
			Consumer<Map<String, Object>> consumer,
			Long mergedPrNumber,
			boolean isMergeCommit) {

		if (unanalyzedCommits.isEmpty()) {
			log.debug("No unanalyzed commits — skipping direct push analysis check");
			return;
		}

		if (rawDiff == null || rawDiff.isBlank()) {
			log.debug("No diff available — skipping direct push analysis");
			return;
		}

		// ── Fast path: PR merge ──────────────────────────────────────────
		// If this branch analysis was triggered by a PR merge, the code was
		// already reviewed during the PR. Always skip — a merge is never a
		// direct push, and any missing analysis will be handled by reconciliation.
		if (mergedPrNumber != null) {
			log.info("Skipping direct push analysis — branch event originates from PR #{} merge (not a direct push)",
					mergedPrNumber);
			return;
		}

		// ── Safety net: merge commit without PR number ───────────────────
		// If the HEAD commit is a merge commit (>1 parent) but the PR number
		// was lost due to webhook race conditions, still skip. A merge commit
		// is NEVER a direct push — it's always the result of a PR merge or
		// manual merge.
		if (isMergeCommit) {
			log.info("Skipping direct push analysis — HEAD commit is a merge commit " +
					"(detected via parent count) but PR number was not resolved. " +
					"This is a PR merge, not a direct push.");
			return;
		}

		// Check if a PR analysis lock is active for this branch.
		// If so, wait — the PR analysis will handle these commits.
		boolean prAnalysisInProgress = analysisLockService.isLocked(
				project.getId(), request.getTargetBranchName(), AnalysisLockType.PR_ANALYSIS);
		if (prAnalysisInProgress) {
			log.info("PR analysis in progress for branch {} — skipping direct push analysis (PR will cover it)",
					request.getTargetBranchName());
			return;
		}

		// Check commit coverage by open/merged PRs
		CommitCoverageService.CoverageResult coverage = commitCoverageService.checkCoverage(
				project.getId(), request.getTargetBranchName(), unanalyzedCommits);

		switch (coverage.status()) {
			case FULLY_COVERED:
				log.info("All {} unanalyzed commits are covered by open PRs — skipping direct push analysis",
						unanalyzedCommits.size());
				return;
			case PARTIALLY_COVERED:
				log.info("{} of {} unanalyzed commits not covered by open PRs — running direct push analysis",
						coverage.uncoveredCommits().size(), unanalyzedCommits.size());
				break;
			case NOT_COVERED:
				log.info("None of {} unanalyzed commits are covered by open PRs — running direct push analysis",
						unanalyzedCommits.size());
				break;
		}

		EventNotificationEmitter.emitStatus(consumer, "direct_push_analysis",
				"Analyzing " + coverage.uncoveredCommits().size()
						+ " uncovered direct push commits via AI");

		try {
			// Build AI analysis request using the VCS-specific service
			VcsAiClientService aiClientService = vcsServiceFactory.getAiClientService(provider);
			Map<String, String> fileContents = Collections.emptyMap();

			// Try to extract file contents from the archive for better line-hash
			// computation
			try {
				VcsRepoInfoImpl vcsRepoInfoImpl = ProjectVcsInfoRetriever.getVcsInfo(project);
				Map<String, String> archiveContents = branchFileOperationsService.downloadBranchArchive(
						vcsRepoInfoImpl, request.getCommitHash(), changedFiles);
				if (!archiveContents.isEmpty()) {
					fileContents = archiveContents;
				}
			} catch (Exception e) {
				log.warn("Failed to download archive for direct push analysis file contents (non-critical): {}",
						e.getMessage());
			}

			List<AiAnalysisRequest> aiRequests = aiClientService.buildDirectPushAnalysisRequests(
					project, request, rawDiff, fileContents, new ArrayList<>(changedFiles));

			// Call the inference orchestrator using single request
			Map<String, Object> aiResponse = aiAnalysisClient.performAnalysis(aiRequests.get(0), event -> {
				try {
					consumer.accept(event);
				} catch (Exception ex) {
					log.debug("Event consumer failed during direct push analysis: {}",
							ex.getMessage());
				}
			});

			// Save the analysis with DetectionSource.DIRECT_PUSH_ANALYSIS
			CodeAnalysis directPushAnalysis = codeAnalysisService.createDirectPushAnalysisFromAiResponse(
					project, aiResponse, request.getTargetBranchName(),
					request.getCommitHash(), fileContents);

			int issuesFound = directPushAnalysis.getIssues() != null
					? directPushAnalysis.getIssues().size()
					: 0;

			// === AST scope enrichment for direct push issues ===
			try {
				if (directPushAnalysis.getIssues() != null && !directPushAnalysis.getIssues().isEmpty()
						&& !fileContents.isEmpty()) {
					astScopeEnricher.enrichWithAstScopes(
							directPushAnalysis.getIssues(), fileContents);
				}
			} catch (Exception astEx) {
				log.warn("AST scope enrichment failed for direct push (non-critical): {}",
						astEx.getMessage());
			}

			log.info("Direct push analysis completed: project={}, branch={}, commit={}, {} issues found",
					project.getId(), request.getTargetBranchName(),
					request.getCommitHash(), issuesFound);

			EventNotificationEmitter.emitStatus(consumer, "direct_push_analysis_complete",
					"Direct push analysis found " + issuesFound + " issues");

		} catch (Exception e) {
			// Direct push analysis failure is non-fatal — reconciliation will still run
			log.warn("Direct push analysis failed (non-fatal, reconciliation will still run): {}",
					e.getMessage(), e);
			EventNotificationEmitter.emitStatus(consumer, "direct_push_analysis_failed",
					"Direct push analysis failed (non-critical): " + e.getMessage());
		}
	}

	// ── RAG incremental update ──────────────────────────────────────────────
	private void performIncrementalRagUpdate(BranchProcessRequest request, Project project, String commitDiff,
			Consumer<Map<String, Object>> consumer) {
		if (ragOperationsService == null) {
			log.info("Skipping RAG incremental update - RagOperationsService not available");
			EventNotificationEmitter.emitStatus(consumer, "rag_skipped",
					"RAG module not deployed — skipping incremental update");
			return;
		}
		try {
			if (!ragOperationsService.isRagEnabled(project)) {
				log.info("Skipping RAG incremental update - RAG not enabled for project={}",
						project.getId());
				EventNotificationEmitter.emitStatus(consumer, "rag_skipped",
						"RAG not enabled for this project — skipping incremental update");
				return;
			}
			if (!ragOperationsService.isRagIndexReady(project)) {
				log.info("Skipping RAG incremental update - RAG index not yet ready for project={}",
						project.getId());
				EventNotificationEmitter.emitStatus(consumer, "rag_skipped",
						"RAG index not yet ready (initial indexing may still be in progress) — skipping incremental update");
				return;
			}

			String targetBranch = request.getTargetBranchName();
			String baseBranch = ragOperationsService.getBaseBranch(project);

			// Health check: verify RAG pipeline is reachable before starting
			if (!ragOperationsService.isRagPipelineHealthy()) {
				log.warn("RAG pipeline is not reachable — skipping incremental update for project={}",
						project.getId());
				EventNotificationEmitter.emitStatus(consumer, "rag_skipped",
						"RAG pipeline not reachable — skipping incremental update");
				return;
			}

			if (targetBranch.equals(baseBranch)) {
				log.info("Main branch push - updating RAG index for project={}, branch={}, commit={}",
						project.getId(), targetBranch, request.getCommitHash());
				EventNotificationEmitter.emitStatus(consumer, "rag_update",
						"Updating RAG index with changed files for main branch push");
				ragOperationsService.triggerIncrementalUpdate(
						project, targetBranch, request.getCommitHash(), commitDiff, consumer);
			} else {
				log.info("Non-main branch push - updating branch index for project={}, branch={}",
						project.getId(), targetBranch);
				ragOperationsService.updateBranchIndex(project, targetBranch, consumer);
			}

			log.info("RAG update completed for project={}, branch={}, commit={}",
					project.getId(), targetBranch, request.getCommitHash());
			EventNotificationEmitter.emitStatus(consumer, "rag_update_complete",
					"RAG index updated successfully for branch: " + targetBranch);
		} catch (Exception e) {
			log.warn("RAG incremental update failed (non-critical): {}", e.getMessage());
			EventNotificationEmitter.emitStatus(consumer, "rag_update_failed",
					"RAG incremental update failed (non-critical): " + e.getMessage());
		}
	}

	// ── Shared small helpers ────────────────────────────────────────────────
	/** Refresh a branch entity from DB and update issue counts. */
	private Branch refreshAndSaveIssueCounts(Branch branch) {
		Branch refreshed = branchRepository.findByIdWithIssues(branch.getId()).orElse(branch);
		refreshed.updateIssueCounts();
		branchRepository.save(refreshed);
		return refreshed;
	}

	private static String shortHash(String hash) {
		return hash != null ? hash.substring(0, Math.min(7, hash.length())) : "null";
	}

	private static boolean isValidPrNumber(Long prNumber) {
		return prNumber != null && prNumber > 0;
	}
}
