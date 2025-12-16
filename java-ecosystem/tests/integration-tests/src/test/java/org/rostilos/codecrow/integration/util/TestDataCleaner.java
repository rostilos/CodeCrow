package org.rostilos.codecrow.integration.util;

import org.rostilos.codecrow.core.persistence.repository.ai.AiConnectionRepository;
import org.rostilos.codecrow.core.persistence.repository.analysis.RagIndexStatusRepository;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchRepository;
import org.rostilos.codecrow.core.persistence.repository.job.JobLogRepository;
import org.rostilos.codecrow.core.persistence.repository.job.JobRepository;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectTokenRepository;
import org.rostilos.codecrow.core.persistence.repository.pullrequest.PullRequestRepository;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsConnectionRepository;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsRepoBindingRepository;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceMemberRepository;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceRepository;
import org.rostilos.codecrow.core.persistence.repository.user.UserRepository;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

/**
 * Utility for cleaning up test data between tests.
 * Ensures test isolation by removing data created during test execution.
 */
@Component
public class TestDataCleaner {

    @Autowired
    private JobLogRepository jobLogRepository;

    @Autowired
    private JobRepository jobRepository;

    @Autowired
    private BranchRepository branchRepository;

    @Autowired
    private PullRequestRepository pullRequestRepository;

    @Autowired
    private RagIndexStatusRepository ragIndexStatusRepository;

    @Autowired
    private ProjectTokenRepository projectTokenRepository;

    @Autowired
    private VcsRepoBindingRepository vcsRepoBindingRepository;

    @Autowired
    private ProjectRepository projectRepository;

    @Autowired
    private AiConnectionRepository aiConnectionRepository;

    @Autowired
    private VcsConnectionRepository vcsConnectionRepository;

    @Autowired
    private WorkspaceMemberRepository workspaceMemberRepository;

    @Autowired
    private WorkspaceRepository workspaceRepository;

    @Autowired
    private UserRepository userRepository;

    @Transactional
    public void cleanAllTestData() {
        cleanAnalysisData();
        cleanProjectData();
        cleanConnectionData();
        cleanWorkspaceData();
    }

    @Transactional
    public void cleanAnalysisData() {
        jobLogRepository.deleteAll();
        jobRepository.deleteAll();
        branchRepository.deleteAll();
        pullRequestRepository.deleteAll();
        ragIndexStatusRepository.deleteAll();
    }

    @Transactional
    public void cleanProjectData() {
        projectTokenRepository.deleteAll();
        vcsRepoBindingRepository.deleteAll();
        // Project bindings (AI and VCS) are deleted via cascade when project is deleted
        projectRepository.deleteAll();
    }

    @Transactional
    public void cleanConnectionData() {
        aiConnectionRepository.deleteAll();
        vcsConnectionRepository.deleteAll();
    }

    @Transactional
    public void cleanWorkspaceData() {
        workspaceMemberRepository.deleteAll();
        workspaceRepository.deleteAll();
    }

    @Transactional
    public void cleanUserData() {
        cleanAnalysisData();
        cleanProjectData();
        cleanConnectionData();
        cleanWorkspaceData();
        userRepository.deleteAll();
    }

    @Transactional
    public void cleanWorkspaceById(Long workspaceId) {
        workspaceMemberRepository.deleteAll(
            workspaceMemberRepository.findByWorkspace_Id(workspaceId)
        );
    }
}
