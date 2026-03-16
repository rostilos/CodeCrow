package org.rostilos.codecrow.testsupport.fixture;

/**
 * Builder for creating test Project entities.
 */
public final class ProjectFixture {

    private String name = "test-project";
    private String repoName = "test-repo";
    private String repoOwner = "test-owner";
    private String defaultBranch = "main";

    private ProjectFixture() {
    }

    public static ProjectFixture aProject() {
        return new ProjectFixture();
    }

    public ProjectFixture withName(String name) {
        this.name = name;
        return this;
    }

    public ProjectFixture withRepoName(String repoName) {
        this.repoName = repoName;
        return this;
    }

    public ProjectFixture withRepoOwner(String repoOwner) {
        this.repoOwner = repoOwner;
        return this;
    }

    public ProjectFixture withDefaultBranch(String defaultBranch) {
        this.defaultBranch = defaultBranch;
        return this;
    }

    public java.util.Map<String, Object> asMap() {
        var map = new java.util.LinkedHashMap<String, Object>();
        map.put("name", name);
        map.put("repoName", repoName);
        map.put("repoOwner", repoOwner);
        map.put("defaultBranch", defaultBranch);
        return map;
    }

    public String getName() { return name; }
    public String getRepoName() { return repoName; }
    public String getRepoOwner() { return repoOwner; }
    public String getDefaultBranch() { return defaultBranch; }
}
