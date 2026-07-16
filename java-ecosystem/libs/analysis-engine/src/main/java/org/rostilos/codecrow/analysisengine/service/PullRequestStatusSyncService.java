package org.rostilos.codecrow.analysisengine.service;

import okhttp3.OkHttpClient;
import org.rostilos.codecrow.analysisengine.processor.VcsRepoInfoImpl;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsOperationsService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.analysisengine.util.ProjectVcsInfoRetriever;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.pullrequest.PullRequest;
import org.rostilos.codecrow.core.model.pullrequest.PullRequestState;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.persistence.repository.pullrequest.PullRequestRepository;
import org.rostilos.codecrow.events.EventNotificationEmitter;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.List;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.function.Consumer;

/**
 * Repairs local PR lifecycle state from the provider source of truth.
 */
@Service
public class PullRequestStatusSyncService {

    private static final Logger log = LoggerFactory.getLogger(PullRequestStatusSyncService.class);

    private final PullRequestRepository pullRequestRepository;
    private final VcsClientProvider vcsClientProvider;
    private final VcsServiceFactory vcsServiceFactory;

    public PullRequestStatusSyncService(
            PullRequestRepository pullRequestRepository,
            VcsClientProvider vcsClientProvider,
            VcsServiceFactory vcsServiceFactory) {
        this.pullRequestRepository = pullRequestRepository;
        this.vcsClientProvider = vcsClientProvider;
        this.vcsServiceFactory = vcsServiceFactory;
    }

    public SyncResult syncOpenPullRequestStates(Project project, Consumer<Map<String, Object>> consumer) {
        List<PullRequest> openPullRequests = pullRequestRepository.findByProject_IdAndState(
                project.getId(), PullRequestState.OPEN);
        return syncOpenPullRequestStates(project, openPullRequests, consumer);
    }

    public SyncResult syncOpenPullRequestStates(
            Project project,
            String targetBranch,
            Consumer<Map<String, Object>> consumer) {
        List<PullRequest> openPullRequests = pullRequestRepository
                .findByProjectIdAndTargetBranchNameAndState(
                        project.getId(), targetBranch, PullRequestState.OPEN);
        return syncOpenPullRequestStates(project, openPullRequests, consumer);
    }

    private SyncResult syncOpenPullRequestStates(
            Project project,
            List<PullRequest> openPullRequests,
            Consumer<Map<String, Object>> consumer) {
        if (openPullRequests == null || openPullRequests.isEmpty()) {
            EventNotificationEmitter.emitStatus(consumer, "sync_pr_status",
                    "No open PR states to sync");
            return SyncResult.empty();
        }

        VcsRepoInfoImpl vcsInfo = ProjectVcsInfoRetriever.getVcsInfo(project);
        OkHttpClient client = vcsClientProvider.getHttpClient(vcsInfo.vcsConnection());
        EVcsProvider provider = ProjectVcsInfoRetriever.getVcsProvider(project);
        VcsOperationsService operationsService = vcsServiceFactory.getOperationsService(provider);

        int checked = 0;
        int stillOpen = 0;
        int markedMerged = 0;
        int markedDeclined = 0;
        int failed = 0;
        Set<Long> mergedPrNumbers = new LinkedHashSet<>();

        EventNotificationEmitter.emitStatus(consumer, "sync_pr_status",
                "Syncing " + openPullRequests.size() + " open PR states from VCS");

        for (PullRequest pullRequest : openPullRequests) {
            if (!isValidPrNumber(pullRequest.getPrNumber())) {
                failed++;
                continue;
            }

            checked++;
            try {
                Optional<PullRequestState> remoteState = operationsService.getPullRequestState(
                        client,
                        vcsInfo.workspace(),
                        vcsInfo.repoSlug(),
                        pullRequest.getPrNumber());

                if (remoteState.isEmpty()) {
                    failed++;
                    continue;
                }

                PullRequestState state = remoteState.get();
                if (state == PullRequestState.OPEN) {
                    stillOpen++;
                    continue;
                }

                pullRequest.setState(state);
                pullRequestRepository.save(pullRequest);
                if (state == PullRequestState.MERGED) {
                    markedMerged++;
                    mergedPrNumbers.add(pullRequest.getPrNumber());
                } else if (state == PullRequestState.DECLINED) {
                    markedDeclined++;
                }
                log.info("Synced PR #{} to {} for project {}",
                        pullRequest.getPrNumber(), state, project.getId());
            } catch (Exception e) {
                failed++;
                log.warn("Failed to sync PR #{} state for project {}: {}",
                        pullRequest.getPrNumber(), project.getId(), e.getMessage());
            }
        }

        EventNotificationEmitter.emitStatus(consumer, "sync_pr_status_complete",
                "PR status sync complete: " + checked + " checked, "
                        + markedMerged + " marked merged, "
                        + markedDeclined + " marked declined, "
                        + stillOpen + " still open, "
                        + failed + " failed");

        return new SyncResult(
                checked,
                stillOpen,
                markedMerged,
                markedDeclined,
                failed,
                Set.copyOf(mergedPrNumbers));
    }

    private static boolean isValidPrNumber(Long prNumber) {
        return prNumber != null && prNumber > 0;
    }

    public record SyncResult(
            int checked,
            int stillOpen,
            int markedMerged,
            int markedDeclined,
            int failed,
            Set<Long> mergedPrNumbers) {

        public SyncResult(
                int checked,
                int stillOpen,
                int markedMerged,
                int markedDeclined,
                int failed) {
            this(checked, stillOpen, markedMerged, markedDeclined, failed, Set.of());
        }

        public static SyncResult empty() {
            return new SyncResult(0, 0, 0, 0, 0, Set.of());
        }
    }
}
