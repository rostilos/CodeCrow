package org.rostilos.codecrow.core.model.project.config;

/**
 * Project-level configuration stored as JSON in the project.configuration column.
 *
 * Currently supports:
 *  - useLocalMcp: when true, MCP servers should prefer local repository access (LocalRepoClient)
 *    when a local repository path is available (for example when analysis is executed from an uploaded archive).
 *  - defaultBranch: optional default branch name for the project (eg "main" or "master").
 */
public record ProjectConfig(
    boolean useLocalMcp,
    String defaultBranch
) {
    public ProjectConfig() {
        this(false, null);
    }
}
