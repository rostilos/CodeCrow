package org.rostilos.codecrow.analysisengine.exception;

public class AnalysisLockedException extends RuntimeException {

    private final String lockType;
    private final String branchName;
    private final Long projectId;

    public AnalysisLockedException(String lockType, String branchName, Long projectId) {
        super(String.format("Analysis is already in progress for project=%d, branch=%s, type=%s",
                projectId, branchName, lockType));
        this.lockType = lockType;
        this.branchName = branchName;
        this.projectId = projectId;
    }

    public String getLockType() {
        return lockType;
    }

    public String getBranchName() {
        return branchName;
    }

    public Long getProjectId() {
        return projectId;
    }
}
