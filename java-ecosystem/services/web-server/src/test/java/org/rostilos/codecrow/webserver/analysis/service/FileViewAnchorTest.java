package org.rostilos.codecrow.webserver.analysis.service;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.branch.*;
import org.rostilos.codecrow.core.model.codeanalysis.*;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.pullrequest.PullRequest;
import org.rostilos.codecrow.core.persistence.repository.branch.*;
import org.rostilos.codecrow.core.persistence.repository.pullrequest.PullRequestRepository;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.filecontent.service.FileSnapshotService;
import org.rostilos.codecrow.webserver.analysis.dto.response.FileViewResponse;
import java.util.*;
import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.*;

class FileViewAnchorTest {
    private final FileSnapshotService snapshots = mock(FileSnapshotService.class);
    private final CodeAnalysisService analyses = mock(CodeAnalysisService.class);
    private final PullRequestRepository prs = mock(PullRequestRepository.class);
    private final BranchRepository branches = mock(BranchRepository.class);
    private final BranchIssueRepository branchIssues = mock(BranchIssueRepository.class);
    private final FileViewService service = new FileViewService(snapshots, analyses, prs, branches, branchIssues);
    private final String path = "changed.java";
    private final String headSource = "current first line\ncurrent second line\n";

    private CodeAnalysisIssue issue(IssueAnchorSide side) {
        var issue = new CodeAnalysisIssue(); issue.setAnchorSide(side); issue.setFilePath(path);
        issue.setTitle(side.name()); issue.setSeverity(IssueSeverity.HIGH); issue.setLineNumber(1);
        issue.setCodeSnippet("removed source, absent from HEAD"); return issue;
    }
    private void headOnly(List<FileViewResponse.InlineIssue> issues) {
        assertThat(issues).extracting(FileViewResponse.InlineIssue::title).containsExactly("HEAD");
    }
    @Test void analysisFileAndBothSnippetEndpointsNeverOverlayBaseFindingsOnHeadSource() {
        var project = mock(Project.class); when(project.getId()).thenReturn(1L);
        var analysis = new CodeAnalysis(); analysis.setProject(project);
        when(analyses.findById(2L)).thenReturn(Optional.of(analysis));
        when(analyses.findIssuesByAnalysisId(2L)).thenReturn(List.of(issue(IssueAnchorSide.BASE), issue(IssueAnchorSide.HEAD)));
        when(snapshots.getFileContent(2L,path)).thenReturn(Optional.of(headSource));
        headOnly(service.getFileView(1L,2L,path).orElseThrow().issues());
        headOnly(service.getFileSnippet(1L,2L,path,1,2).orElseThrow().issues());
        headOnly(service.getFileSnippetByRange(1L,2L,path,1,2).orElseThrow().issues());
    }
    @Test void prFileAndBothSnippetEndpointsNeverOverlayBaseFindingsOnHeadSource() {
        var pr = mock(PullRequest.class); when(pr.getId()).thenReturn(3L);
        when(prs.findByPrNumberAndProject_id(4L,1L)).thenReturn(Optional.of(pr));
        when(analyses.findIssuesByPrNumberAndFilePathLatestVersion(1L,4L,path))
                .thenReturn(List.of(issue(IssueAnchorSide.BASE), issue(IssueAnchorSide.HEAD)));
        when(snapshots.getFileContentForPr(3L,path)).thenReturn(Optional.of(headSource));
        headOnly(service.getPrFileView(1L,4L,path).orElseThrow().issues());
        headOnly(service.getPrFileSnippet(1L,4L,path,1,2).orElseThrow().issues());
        headOnly(service.getPrFileSnippetByRange(1L,4L,path,1,2).orElseThrow().issues());
    }
    @Test void branchFileAndBothSnippetEndpointsNeverOverlayBaseFindingsOnHeadSource() {
        var branch = mock(Branch.class); when(branch.getId()).thenReturn(5L);
        when(branches.findByProjectIdAndBranchName(1L,"main")).thenReturn(Optional.of(branch));
        var base = BranchIssue.fromCodeAnalysisIssue(issue(IssueAnchorSide.BASE),branch);
        var head = BranchIssue.fromCodeAnalysisIssue(issue(IssueAnchorSide.HEAD),branch);
        when(branchIssues.findByBranchIdAndFilePath(5L,path)).thenReturn(List.of(base,head));
        when(snapshots.getFileContentForBranch(1L,"main",path)).thenReturn(Optional.of(headSource));
        headOnly(service.getBranchFileView(1L,"main",path).orElseThrow().issues());
        headOnly(service.getBranchFileSnippet(1L,"main",path,1,2).orElseThrow().issues());
        headOnly(service.getBranchFileSnippetByRange(1L,"main",path,1,2).orElseThrow().issues());
    }
}
