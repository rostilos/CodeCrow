package org.rostilos.codecrow.testsupport.fixture;

/**
 * Builder for creating test Workspace entities.
 */
public final class WorkspaceFixture {

    private String name = "test-workspace";
    private String slug = "test-workspace";

    private WorkspaceFixture() {
    }

    public static WorkspaceFixture aWorkspace() {
        return new WorkspaceFixture();
    }

    public WorkspaceFixture withName(String name) {
        this.name = name;
        return this;
    }

    public WorkspaceFixture withSlug(String slug) {
        this.slug = slug;
        return this;
    }

    public java.util.Map<String, Object> asMap() {
        var map = new java.util.LinkedHashMap<String, Object>();
        map.put("name", name);
        map.put("slug", slug);
        return map;
    }

    public String getName() { return name; }
    public String getSlug() { return slug; }
}
