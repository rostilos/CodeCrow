package org.rostilos.codecrow.mcp;

import java.util.ArrayList;
import java.util.List;

import org.rostilos.codecrow.mcp.generic.VcsMcpClientFactory;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import com.fasterxml.jackson.databind.ObjectMapper;

import io.modelcontextprotocol.server.McpServer;
import io.modelcontextprotocol.server.McpServerFeatures;
import io.modelcontextprotocol.server.McpSyncServer;
import io.modelcontextprotocol.server.transport.StdioServerTransportProvider;
import io.modelcontextprotocol.spec.McpSchema;
import io.modelcontextprotocol.spec.McpSchema.CallToolResult;
import io.modelcontextprotocol.spec.McpSchema.TextContent;
import io.modelcontextprotocol.spec.McpSchema.Tool;

public class McpStdioServer {

    private static final Logger log = LoggerFactory.getLogger(McpStdioServer.class);
    private static final ObjectMapper objectMapper = new ObjectMapper();
    private static final McpTools mcpTools = new McpTools(new VcsMcpClientFactory());

    public static void main(String[] args) {
        try {
            var transportProvider = new StdioServerTransportProvider(objectMapper);

            McpSyncServer syncServer = McpServer.sync(transportProvider)
                    .serverInfo("vcs-mcp-server", "1.0.0")
                    .capabilities(McpSchema.ServerCapabilities.builder()
                            .tools(true)
                            .prompts(true)
                            .resources(true, true)
                            .logging()
                            .build())
                    .tools(getToolSpecifications())
                    .build();

            log.info("VCS MCP server running on stdio...");
        } catch (Exception e) {
            log.error("Server initialization error", e);
            System.exit(1);
        }
    }

    private static List<McpServerFeatures.SyncToolSpecification> getToolSpecifications() {
        List<Tool> tools = defineTools();
        List<McpServerFeatures.SyncToolSpecification> specifications = new ArrayList<>();

        for (Tool tool : tools) {
            McpServerFeatures.SyncToolSpecification spec = new McpServerFeatures.SyncToolSpecification(
                    tool,
                    (exchange, arguments) -> {
                        try {
                            log.info(exchange.toString());
                            Object result = mcpTools.execute(tool.name(), arguments);
                            String jsonResult = objectMapper.writeValueAsString(result);
                            return new CallToolResult(List.of(new TextContent(jsonResult)), false);
                        } catch (Exception e) {
                            log.error("Tool execution error for " + tool.name(), e);
                            return new CallToolResult(List.of(new TextContent("Error executing tool: " + e.getMessage())), true);
                        }
                    }
            );
            specifications.add(spec);
        }

        return specifications;
    }

    // New method to define tools
    private static List<Tool> defineTools() {
        List<Tool> tools = new ArrayList<>();

        // Schema for listRepositories
        String listRepositoriesSchema = """
                {
                  "type": "object",
                  "properties": {
                    "workspace": {
                      "type": "string",
                      "description": "The workspace ID (Bitbucket) or owner username/organization (GitHub). Do not include the repository name here."
                    },
                    "limit": {
                      "type": "integer",
                      "description": "The maximum number of repositories to return (optional)."
                    }
                  },
                  "required": ["workspace"]
                }
                """;
        tools.add(new Tool("listRepositories", "List repositories in a workspace.", listRepositoriesSchema));

        String getRepositorySchema = """
                {
                  "type": "object",
                  "properties": {
                    "workspace": {
                      "type": "string",
                      "description": "The workspace ID (Bitbucket) or owner username/organization (GitHub). Do not include the repository name here."
                    },
                    "repoSlug": {
                      "type": "string",
                      "description": "The repository slug."
                    }
                  },
                  "required": ["workspace", "repoSlug"]
                }
                """;
        tools.add(new Tool("getRepository", "Get details of a specific repository.", getRepositorySchema));

        String getPullRequestsSchema = """
                {
                  "type": "object",
                  "properties": {
                    "workspace": {
                      "type": "string",
                      "description": "The workspace ID (Bitbucket) or owner username/organization (GitHub). Do not include the repository name here."
                    },
                    "repoSlug": {
                      "type": "string",
                      "description": "The repository slug."
                    },
                    "state": {
                      "type": "string",
                      "description": "The state of the pull requests (e.g., OPEN, MERGED, DECLINED) (optional)."
                    },
                    "limit": {
                      "type": "integer",
                      "description": "The maximum number of pull requests to return (optional)."
                    }
                  },
                  "required": ["workspace", "repoSlug"]
                }
                """;
        tools.add(new Tool("getPullRequests", "Get pull requests for a repository.", getPullRequestsSchema));

        String createPullRequestSchema = """
                {
                  "type": "object",
                  "properties": {
                    "workspace": {
                      "type": "string",
                      "description": "The workspace ID (Bitbucket) or owner username/organization (GitHub). Do not include the repository name here."
                    },
                    "repoSlug": {
                      "type": "string",
                      "description": "The repository slug."
                    },
                    "title": {
                      "type": "string",
                      "description": "The title of the pull request."
                    },
                    "description": {
                      "type": "string",
                      "description": "The description of the pull request (optional)."
                    },
                    "sourceBranch": {
                      "type": "string",
                      "description": "The name of the source branch."
                    },
                    "targetBranch": {
                      "type": "string",
                      "description": "The name of the target branch."
                    },
                    "reviewers": {
                      "type": "array",
                      "items": {
                        "type": "string"
                      },
                      "description": "A list of UUIDs or usernames of reviewers (optional)."
                    }
                  },
                  "required": ["workspace", "repoSlug", "title", "sourceBranch", "targetBranch"]
                }
                """;
        tools.add(new Tool("createPullRequest", "Create a new pull request.", createPullRequestSchema));

        String getPullRequestSchema = """
                {
                  "type": "object",
                  "properties": {
                    "workspace": {
                      "type": "string",
                      "description": "The workspace ID (Bitbucket) or owner username/organization (GitHub). Do not include the repository name here."
                    },
                    "repoSlug": {
                      "type": "string",
                      "description": "The repository slug."
                    },
                    "pullRequestId": {
                      "type": "string",
                      "description": "The ID of the pull request."
                    }
                  },
                  "required": ["workspace", "repoSlug", "pullRequestId"]
                }
                """;
        tools.add(new Tool("getPullRequest", "Get details of a specific pull request.", getPullRequestSchema));

        String updatePullRequestSchema = """
                {
                  "type": "object",
                  "properties": {
                    "workspace": {
                      "type": "string",
                      "description": "The workspace ID (Bitbucket) or owner username/organization (GitHub). Do not include the repository name here."
                    },
                    "repoSlug": {
                      "type": "string",
                      "description": "The repository slug."
                    },
                    "pullRequestId": {
                      "type": "string",
                      "description": "The ID of the pull request."
                    },
                    "title": {
                      "type": "string",
                      "description": "The new title of the pull request (optional)."
                    },
                    "description": {
                      "type": "string",
                      "description": "The new description of the pull request (optional)."
                    }
                  },
                  "required": ["workspace", "repoSlug", "pullRequestId"]
                }
                """;
        tools.add(new Tool("updatePullRequest", "Update an existing pull request.", updatePullRequestSchema));

        String getPullRequestActivitySchema = """
                {
                  "type": "object",
                  "properties": {
                    "workspace": {
                      "type": "string",
                      "description": "The workspace ID (Bitbucket) or owner username/organization (GitHub). Do not include the repository name here."
                    },
                    "repoSlug": {
                      "type": "string",
                      "description": "The repository slug."
                    },
                    "pullRequestId": {
                      "type": "string",
                      "description": "The ID of the pull request."
                    }
                  },
                  "required": ["workspace", "repoSlug", "pullRequestId"]
                }
                """;
        tools.add(new Tool("getPullRequestActivity", "Get activity for a pull request.", getPullRequestActivitySchema));

        String approvePullRequestSchema = """
                {
                  "type": "object",
                  "properties": {
                    "workspace": {
                      "type": "string",
                      "description": "The workspace ID (Bitbucket) or owner username/organization (GitHub). Do not include the repository name here."
                    },
                    "repoSlug": {
                      "type": "string",
                      "description": "The repository slug."
                    },
                    "pullRequestId": {
                      "type": "string",
                      "description": "The ID of the pull request."
                    }
                  },
                  "required": ["workspace", "repoSlug", "pullRequestId"]
                }
                """;
        tools.add(new Tool("approvePullRequest", "Approve a pull request.", approvePullRequestSchema));

        String unapprovePullRequestSchema = """
                {
                  "type": "object",
                  "properties": {
                    "workspace": {
                      "type": "string",
                      "description": "The workspace ID (Bitbucket) or owner username/organization (GitHub). Do not include the repository name here."
                    },
                    "repoSlug": {
                      "type": "string",
                      "description": "The repository slug."
                    },
                    "pullRequestId": {
                      "type": "string",
                      "description": "The ID of the pull request."
                    }
                  },
                  "required": ["workspace", "repoSlug", "pullRequestId"]
                }
                """;
        tools.add(new Tool("unapprovePullRequest", "Unapprove a pull request.", unapprovePullRequestSchema));

        String declinePullRequestSchema = """
                {
                  "type": "object",
                  "properties": {
                    "workspace": {
                      "type": "string",
                      "description": "The workspace ID (Bitbucket) or owner username/organization (GitHub). Do not include the repository name here."
                    },
                    "repoSlug": {
                      "type": "string",
                      "description": "The repository slug."
                    },
                    "pullRequestId": {
                      "type": "string",
                      "description": "The ID of the pull request."
                    },
                    "message": {
                      "type": "string",
                      "description": "An optional message explaining the decline."
                    }
                  },
                  "required": ["workspace", "repoSlug", "pullRequestId"]
                }
                """;
        tools.add(new Tool("declinePullRequest", "Decline a pull request.", declinePullRequestSchema));

        String mergePullRequestSchema = """
                {
                  "type": "object",
                  "properties": {
                    "workspace": {
                      "type": "string",
                      "description": "The workspace ID (Bitbucket) or owner username/organization (GitHub). Do not include the repository name here."
                    },
                    "repoSlug": {
                      "type": "string",
                      "description": "The repository slug."
                    },
                    "pullRequestId": {
                      "type": "string",
                      "description": "The ID of the pull request."
                    },
                    "message": {
                      "type": "string",
                      "description": "An optional message for the merge commit."
                    },
                    "strategy": {
                      "type": "string",
                      "description": "The merge strategy to use (e.g., merge_commit, squash, fast_forward) (optional)."
                    }
                  },
                  "required": ["workspace", "repoSlug", "pullRequestId"]
                }
                """;
        tools.add(new Tool("mergePullRequest", "Merge a pull request.", mergePullRequestSchema));

        String getPullRequestCommentsSchema = """
                {
                  "type": "object",
                  "properties": {
                    "workspace": {
                      "type": "string",
                      "description": "The workspace ID (Bitbucket) or owner username/organization (GitHub). Do not include the repository name here."
                    },
                    "repoSlug": {
                      "type": "string",
                      "description": "The repository slug."
                    },
                    "pullRequestId": {
                      "type": "string",
                      "description": "The ID of the pull request."
                    }
                  },
                  "required": ["workspace", "repoSlug", "pullRequestId"]
                }
                """;
        tools.add(new Tool("getPullRequestComments", "Get comments for a pull request.", getPullRequestCommentsSchema));

        String getPullRequestDiffSchema = """
                {
                  "type": "object",
                  "properties": {
                    "workspace": {
                      "type": "string",
                      "description": "The workspace ID (Bitbucket) or owner username/organization (GitHub). Do not include the repository name here."
                    },
                    "repoSlug": {
                      "type": "string",
                      "description": "The repository slug."
                    },
                    "pullRequestId": {
                      "type": "string",
                      "description": "The ID of the pull request."
                    }
                  },
                  "required": ["workspace", "repoSlug", "pullRequestId"]
                }
                """;
        tools.add(new Tool("getPullRequestDiff", "Get diff for a pull request.", getPullRequestDiffSchema));

        String getPullRequestCommitsSchema = """
                {
                  "type": "object",
                  "properties": {
                    "workspace": {
                      "type": "string",
                      "description": "The workspace ID (Bitbucket) or owner username/organization (GitHub). Do not include the repository name here."
                    },
                    "repoSlug": {
                      "type": "string",
                      "description": "The repository slug."
                    },
                    "pullRequestId": {
                      "type": "string",
                      "description": "The ID of the pull request."
                    }
                  },
                  "required": ["workspace", "repoSlug", "pullRequestId"]
                }
                """;
        tools.add(new Tool("getPullRequestCommits", "Get commits for a pull request.", getPullRequestCommitsSchema));

        String getRepositoryBranchingModelSchema = """
                {
                  "type": "object",
                  "properties": {
                    "workspace": {
                      "type": "string",
                      "description": "The workspace ID (Bitbucket) or owner username/organization (GitHub). Do not include the repository name here."
                    },
                    "repoSlug": {
                      "type": "string",
                      "description": "The repository slug."
                    }
                  },
                  "required": ["workspace", "repoSlug"]
                }
                """;
        tools.add(new Tool("getRepositoryBranchingModel", "Get branching model for a repository.", getRepositoryBranchingModelSchema));

        String getRepositoryBranchingModelSettingsSchema = """
                {
                  "type": "object",
                  "properties": {
                    "workspace": {
                      "type": "string",
                      "description": "The workspace ID (Bitbucket) or owner username/organization (GitHub). Do not include the repository name here."
                    },
                    "repoSlug": {
                      "type": "string",
                      "description": "The repository slug."
                    }
                  },
                  "required": ["workspace", "repoSlug"]
                }
                """;
        tools.add(new Tool("getRepositoryBranchingModelSettings", "Get branching model settings for a repository.", getRepositoryBranchingModelSettingsSchema));

        String updateRepositoryBranchingModelSettingsSchema = """
                {
                  "type": "object",
                  "properties": {
                    "workspace": {
                      "type": "string",
                      "description": "The workspace ID (Bitbucket) or owner username/organization (GitHub). Do not include the repository name here."
                    },
                    "repoSlug": {
                      "type": "string",
                      "description": "The repository slug."
                    },
                    "development": {
                      "type": "object",
                      "description": "Development branch configuration (optional)."
                    },
                    "production": {
                      "type": "object",
                      "description": "Production branch configuration (optional)."
                    },
                    "branchTypes": {
                      "type": "array",
                      "items": {
                        "type": "object"
                      },
                      "description": "List of branch type configurations (optional)."
                    }
                  },
                  "required": ["workspace", "repoSlug"]
                }
                """;
        tools.add(new Tool("updateRepositoryBranchingModelSettings", "Update branching model settings for a repository.", updateRepositoryBranchingModelSettingsSchema));

        String getEffectiveRepositoryBranchingModelSchema = """
                {
                  "type": "object",
                  "properties": {
                    "workspace": {
                      "type": "string",
                      "description": "The workspace ID (Bitbucket) or owner username/organization (GitHub). Do not include the repository name here."
                    },
                    "repoSlug": {
                      "type": "string",
                      "description": "The repository slug."
                    }
                  },
                  "required": ["workspace", "repoSlug"]
                }
                """;
        tools.add(new Tool("getEffectiveRepositoryBranchingModel", "Get effective branching model for a repository.", getEffectiveRepositoryBranchingModelSchema));

        String getProjectBranchingModelSchema = """
                {
                  "type": "object",
                  "properties": {
                    "workspace": {
                      "type": "string",
                      "description": "The workspace ID (Bitbucket) or owner username/organization (GitHub). Do not include the repository name here."
                    },
                    "projectKey": {
                      "type": "string",
                      "description": "The project key."
                    }
                  },
                  "required": ["workspace", "projectKey"]
                }
                """;
        tools.add(new Tool("getProjectBranchingModel", "Get branching model for a project.", getProjectBranchingModelSchema));

        String getProjectBranchingModelSettingsSchema = """
                {
                  "type": "object",
                  "properties": {
                    "workspace": {
                      "type": "string",
                      "description": "The workspace ID (Bitbucket) or owner username/organization (GitHub). Do not include the repository name here."
                    },
                    "projectKey": {
                      "type": "string",
                      "description": "The project key."
                    }
                  },
                  "required": ["workspace", "projectKey"]
                }
                """;
        tools.add(new Tool("getProjectBranchingModelSettings", "Get branching model settings for a project.", getProjectBranchingModelSettingsSchema));

        String updateProjectBranchingModelSettingsSchema = """
                {
                  "type": "object",
                  "properties": {
                    "workspace": {
                      "type": "string",
                      "description": "The workspace ID (Bitbucket) or owner username/organization (GitHub). Do not include the repository name here."
                    },
                    "projectKey": {
                      "type": "string",
                      "description": "The project key."
                    },
                    "development": {
                      "type": "object",
                      "description": "Development branch configuration (optional)."
                    },
                    "production": {
                      "type": "object",
                      "description": "Production branch configuration (optional)."
                    },
                    "branchTypes": {
                      "type": "array",
                      "items": {
                        "type": "object"
                      },
                      "description": "List of branch type configurations (optional)."
                    }
                  },
                  "required": ["workspace", "projectKey"]
                }
                """;
        tools.add(new Tool("updateProjectBranchingModelSettings", "Update branching model settings for a project.", updateProjectBranchingModelSettingsSchema));


        String getBranchFileContentSchema = """
                {
                  "type": "object",
                  "properties": {
                    "workspace": {
                      "type": "string",
                      "description": "The workspace ID (Bitbucket) or owner username/organization (GitHub). Do not include the repository name here."
                    },
                    "repoSlug": {
                      "type": "string",
                      "description": "The repository slug."
                    },
                    "branch": {
                      "type": "string",
                      "description": "Development branch or commit"
                    },
                    "filePath": {
                      "type": "string",
                      "description": "File Path."
                    }
                  },
                  "required": ["workspace", "repoSlug", "branch", "filePath"]
                }
                """;
        tools.add(new Tool("getBranchFileContent", "Get full file content from specified branch or commit.", getBranchFileContentSchema));


        String getRootDirectorySchema = """
                {
                  "type": "object",
                  "properties": {
                    "workspace": {
                      "type": "string",
                      "description": "The workspace ID (Bitbucket) or owner username/organization (GitHub). Do not include the repository name here."
                    },
                    "projectKey": {
                      "type": "string",
                      "description": "The project key."
                    },
                    "branch": {
                      "type": "string",
                      "description": "Development branch or commit"
                    }
                  },
                  "required": ["workspace", "projectKey", "branch"]
                }
                """;
        tools.add(new Tool("getRootDirectory", "Get branch root directory.", getRootDirectorySchema));

        String getDirectoryByPathSchema = """
                {
                  "type": "object",
                  "properties": {
                    "workspace": {
                      "type": "string",
                      "description": "The workspace ID (Bitbucket) or owner username/organization (GitHub). Do not include the repository name here."
                    },
                    "projectKey": {
                      "type": "string",
                      "description": "The project key."
                    },
                    "branch": {
                      "type": "string",
                      "description": "Development branch or commit"
                    },
                    "dirPath": {
                      "type": "string",
                      "description": "Directory path"
                    }
                  },
                  "required": ["workspace", "projectKey", "branch", "dirPath"]
                }
                """;
        tools.add(new Tool("getDirectoryByPath", "Get branch directory by path.", getDirectoryByPathSchema));

        return tools;
    }
}
