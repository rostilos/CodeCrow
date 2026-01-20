package org.rostilos.codecrow.analysisengine.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.pullrequest.PullRequest;
import org.rostilos.codecrow.core.persistence.repository.pullrequest.PullRequestRepository;

import java.lang.reflect.Field;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class PullRequestServiceTest {

    @Mock
    private PullRequestRepository pullRequestRepository;

    @InjectMocks
    private PullRequestService pullRequestService;

    private Project testProject;

    @BeforeEach
    void setUp() throws Exception {
        testProject = new Project();
        setId(testProject, 1L);
        testProject.setName("test-project");
    }

    private void setId(Object obj, Long id) throws Exception {
        Field field = obj.getClass().getDeclaredField("id");
        field.setAccessible(true);
        field.set(obj, id);
    }

    @Test
    void testCreateOrUpdatePullRequest_NewPullRequest_Creates() {
        Long projectId = 1L;
        Long prNumber = 123L;
        String commitHash = "abc123";
        String sourceBranch = "feature";
        String targetBranch = "main";

        when(pullRequestRepository.findByPrNumberAndProject_id(prNumber, projectId))
                .thenReturn(Optional.empty());
        when(pullRequestRepository.save(any(PullRequest.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));

        PullRequest result = pullRequestService.createOrUpdatePullRequest(
                projectId, prNumber, commitHash, sourceBranch, targetBranch, testProject
        );

        assertThat(result).isNotNull();
        assertThat(result.getProject()).isEqualTo(testProject);
        assertThat(result.getPrNumber()).isEqualTo(prNumber);
        assertThat(result.getCommitHash()).isEqualTo(commitHash);
        assertThat(result.getSourceBranchName()).isEqualTo(sourceBranch);
        assertThat(result.getTargetBranchName()).isEqualTo(targetBranch);

        ArgumentCaptor<PullRequest> prCaptor = ArgumentCaptor.forClass(PullRequest.class);
        verify(pullRequestRepository).save(prCaptor.capture());
        
        PullRequest saved = prCaptor.getValue();
        assertThat(saved.getProject()).isEqualTo(testProject);
        assertThat(saved.getPrNumber()).isEqualTo(prNumber);
    }

    @Test
    void testCreateOrUpdatePullRequest_ExistingPullRequest_Updates() throws Exception {
        Long projectId = 1L;
        Long prNumber = 123L;
        String oldCommitHash = "old123";
        String newCommitHash = "new456";
        String sourceBranch = "feature";
        String targetBranch = "main";

        PullRequest existingPr = new PullRequest();
        setId(existingPr, 10L);
        existingPr.setProject(testProject);
        existingPr.setPrNumber(prNumber);
        existingPr.setCommitHash(oldCommitHash);
        existingPr.setSourceBranchName("old-source");
        existingPr.setTargetBranchName("old-target");

        when(pullRequestRepository.findByPrNumberAndProject_id(prNumber, projectId))
                .thenReturn(Optional.of(existingPr));
        when(pullRequestRepository.save(any(PullRequest.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));

        PullRequest result = pullRequestService.createOrUpdatePullRequest(
                projectId, prNumber, newCommitHash, sourceBranch, targetBranch, testProject
        );

        assertThat(result).isNotNull();
        assertThat(result.getId()).isEqualTo(10L);
        assertThat(result.getCommitHash()).isEqualTo(newCommitHash);
        assertThat(result.getSourceBranchName()).isEqualTo("old-source");
        assertThat(result.getTargetBranchName()).isEqualTo("old-target");

        verify(pullRequestRepository).findByPrNumberAndProject_id(prNumber, projectId);
        verify(pullRequestRepository).save(existingPr);
        verify(pullRequestRepository, never()).save(argThat(pr -> pr.getId() == null));
    }

    @Test
    void testCreateOrUpdatePullRequest_ExistingPullRequest_OnlyUpdatesCommitHash() throws Exception {
        Long projectId = 1L;
        Long prNumber = 456L;
        String newCommitHash = "updated-hash";

        PullRequest existingPr = new PullRequest();
        setId(existingPr, 20L);
        existingPr.setPrNumber(prNumber);
        existingPr.setCommitHash("original-hash");
        existingPr.setProject(testProject);

        when(pullRequestRepository.findByPrNumberAndProject_id(prNumber, projectId))
                .thenReturn(Optional.of(existingPr));
        when(pullRequestRepository.save(existingPr)).thenReturn(existingPr);

        pullRequestService.createOrUpdatePullRequest(
                projectId, prNumber, newCommitHash, "ignored-source", "ignored-target", testProject
        );

        assertThat(existingPr.getCommitHash()).isEqualTo(newCommitHash);
        verify(pullRequestRepository).save(existingPr);
    }

    @Test
    void testCreateOrUpdatePullRequest_NewPullRequest_SetsAllFields() {
        Long projectId = 1L;
        Long prNumber = 789L;
        String commitHash = "commit789";
        String sourceBranch = "develop";
        String targetBranch = "release";

        when(pullRequestRepository.findByPrNumberAndProject_id(prNumber, projectId))
                .thenReturn(Optional.empty());
        when(pullRequestRepository.save(any(PullRequest.class)))
                .thenAnswer(invocation -> {
                    PullRequest pr = invocation.getArgument(0);
                    try {
                        setId(pr, 99L);
                    } catch (Exception e) {
                        throw new RuntimeException(e);
                    }
                    return pr;
                });

        PullRequest result = pullRequestService.createOrUpdatePullRequest(
                projectId, prNumber, commitHash, sourceBranch, targetBranch, testProject
        );

        assertThat(result.getId()).isEqualTo(99L);
        assertThat(result.getProject()).isEqualTo(testProject);
        assertThat(result.getPrNumber()).isEqualTo(prNumber);
        assertThat(result.getCommitHash()).isEqualTo(commitHash);
        assertThat(result.getSourceBranchName()).isEqualTo(sourceBranch);
        assertThat(result.getTargetBranchName()).isEqualTo(targetBranch);
    }

    @Test
    void testCreateOrUpdatePullRequest_DifferentProjects_CreatesSeparatePRs() throws Exception {
        Project project1 = new Project();
        setId(project1, 1L);
        
        Project project2 = new Project();
        setId(project2, 2L);

        Long prNumber = 100L;
        String commitHash = "abc";

        when(pullRequestRepository.findByPrNumberAndProject_id(prNumber, 1L))
                .thenReturn(Optional.empty());
        when(pullRequestRepository.findByPrNumberAndProject_id(prNumber, 2L))
                .thenReturn(Optional.empty());
        when(pullRequestRepository.save(any(PullRequest.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));

        PullRequest pr1 = pullRequestService.createOrUpdatePullRequest(
                1L, prNumber, commitHash, "src", "tgt", project1
        );
        PullRequest pr2 = pullRequestService.createOrUpdatePullRequest(
                2L, prNumber, commitHash, "src", "tgt", project2
        );

        assertThat(pr1.getProject()).isEqualTo(project1);
        assertThat(pr2.getProject()).isEqualTo(project2);
        verify(pullRequestRepository, times(2)).save(any(PullRequest.class));
    }
}
