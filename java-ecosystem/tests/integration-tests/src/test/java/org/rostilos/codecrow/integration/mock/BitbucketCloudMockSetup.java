package org.rostilos.codecrow.integration.mock;

import com.github.tomakehurst.wiremock.WireMockServer;
import com.github.tomakehurst.wiremock.client.WireMock;

import static com.github.tomakehurst.wiremock.client.WireMock.*;

/**
 * Helper class for setting up WireMock stubs for Bitbucket Cloud API.
 */
public class BitbucketCloudMockSetup {

    private final WireMockServer server;

    public BitbucketCloudMockSetup(WireMockServer server) {
        this.server = server;
    }

    public void setupValidUserResponse() {
        server.stubFor(get(urlPathEqualTo("/2.0/user"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("""
                            {
                                "uuid": "{test-user-uuid}",
                                "username": "test-user",
                                "display_name": "Test User",
                                "account_id": "test-account-id"
                            }
                            """)));
    }

    public void setupWorkspacesResponse(String workspaceSlug, String workspaceName, String workspaceUuid) {
        server.stubFor(get(urlPathEqualTo("/2.0/workspaces"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody(String.format("""
                            {
                                "values": [
                                    {
                                        "uuid": "%s",
                                        "slug": "%s",
                                        "name": "%s"
                                    }
                                ],
                                "size": 1,
                                "page": 1,
                                "pagelen": 10,
                                "next": null
                            }
                            """, workspaceUuid, workspaceSlug, workspaceName))));
    }

    public void setupRepositoriesResponse(String workspaceSlug) {
        server.stubFor(get(urlPathMatching("/2.0/repositories/" + workspaceSlug + ".*"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("""
                            {
                                "values": [
                                    {
                                        "uuid": "{repo-uuid-1}",
                                        "slug": "test-repo-1",
                                        "name": "Test Repository 1",
                                        "full_name": "test-workspace/test-repo-1",
                                        "is_private": true,
                                        "mainbranch": {
                                            "name": "main",
                                            "type": "branch"
                                        }
                                    },
                                    {
                                        "uuid": "{repo-uuid-2}",
                                        "slug": "test-repo-2",
                                        "name": "Test Repository 2",
                                        "full_name": "test-workspace/test-repo-2",
                                        "is_private": false,
                                        "mainbranch": {
                                            "name": "master",
                                            "type": "branch"
                                        }
                                    }
                                ],
                                "size": 2,
                                "page": 1,
                                "pagelen": 10,
                                "next": null
                            }
                            """)));
    }

    public void setupRepositoryResponse(String workspaceSlug, String repoSlug) {
        server.stubFor(get(urlPathEqualTo("/2.0/repositories/" + workspaceSlug + "/" + repoSlug))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody(String.format("""
                            {
                                "uuid": "{repo-uuid}",
                                "slug": "%s",
                                "name": "Test Repository",
                                "full_name": "%s/%s",
                                "is_private": true,
                                "mainbranch": {
                                    "name": "main",
                                    "type": "branch"
                                },
                                "links": {
                                    "html": {
                                        "href": "https://bitbucket.org/%s/%s"
                                    }
                                }
                            }
                            """, repoSlug, workspaceSlug, repoSlug, workspaceSlug, repoSlug))));
    }

    public void setupCommitResponse(String workspaceSlug, String repoSlug, String branch, String commitHash) {
        server.stubFor(get(urlPathEqualTo("/2.0/repositories/" + workspaceSlug + "/" + repoSlug + "/refs/branches/" + branch))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody(String.format("""
                            {
                                "name": "%s",
                                "target": {
                                    "hash": "%s",
                                    "date": "2024-01-15T10:30:00+00:00",
                                    "message": "Test commit message"
                                }
                            }
                            """, branch, commitHash))));
    }

    public void setupDiffResponse(String workspaceSlug, String repoSlug, String spec) {
        server.stubFor(get(urlPathEqualTo("/2.0/repositories/" + workspaceSlug + "/" + repoSlug + "/diff/" + spec))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "text/plain")
                        .withBody("""
                            diff --git a/src/main.java b/src/main.java
                            index abc123..def456 100644
                            --- a/src/main.java
                            +++ b/src/main.java
                            @@ -1,5 +1,7 @@
                             public class Main {
                            +    // Added new comment
                                 public static void main(String[] args) {
                            +        System.out.println("Hello, World!");
                                 }
                             }
                            """)));
    }

    public void setupFileContentResponse(String workspaceSlug, String repoSlug, String filePath, String content) {
        server.stubFor(get(urlPathEqualTo("/2.0/repositories/" + workspaceSlug + "/" + repoSlug + "/src/main/" + filePath))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "text/plain")
                        .withBody(content)));
    }

    public void setupWebhookCreation(String workspaceSlug, String repoSlug) {
        server.stubFor(post(urlPathEqualTo("/2.0/repositories/" + workspaceSlug + "/" + repoSlug + "/hooks"))
                .willReturn(aResponse()
                        .withStatus(201)
                        .withHeader("Content-Type", "application/json")
                        .withBody("""
                            {
                                "uuid": "{webhook-uuid}",
                                "description": "CodeCrow Webhook",
                                "active": true,
                                "url": "https://codecrow.example.com/webhook"
                            }
                            """)));
    }

    public void setupArchiveDownload(String workspaceSlug, String repoSlug, String branch, byte[] archiveContent) {
        server.stubFor(get(urlPathEqualTo("/2.0/repositories/" + workspaceSlug + "/" + repoSlug + "/downloads"))
                .withQueryParam("at", equalTo(branch))
                .withQueryParam("format", equalTo("zip"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/zip")
                        .withBody(archiveContent)));
    }

    public void setupPullRequestResponse(String workspaceSlug, String repoSlug, int prId) {
        server.stubFor(get(urlPathEqualTo("/2.0/repositories/" + workspaceSlug + "/" + repoSlug + "/pullrequests/" + prId))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody(String.format("""
                            {
                                "id": %d,
                                "title": "Test Pull Request",
                                "description": "This is a test PR",
                                "state": "OPEN",
                                "source": {
                                    "branch": {
                                        "name": "feature/test-branch"
                                    },
                                    "commit": {
                                        "hash": "abc123"
                                    }
                                },
                                "destination": {
                                    "branch": {
                                        "name": "main"
                                    },
                                    "commit": {
                                        "hash": "def456"
                                    }
                                },
                                "author": {
                                    "display_name": "Test Author"
                                }
                            }
                            """, prId))));
    }

    public void setupInvalidCredentials() {
        server.stubFor(get(urlPathEqualTo("/2.0/user"))
                .willReturn(aResponse()
                        .withStatus(401)
                        .withHeader("Content-Type", "application/json")
                        .withBody("""
                            {
                                "type": "error",
                                "error": {
                                    "message": "Invalid credentials"
                                }
                            }
                            """)));
    }

    public void setupRateLimitResponse() {
        server.stubFor(get(urlPathMatching("/2.0/.*"))
                .willReturn(aResponse()
                        .withStatus(429)
                        .withHeader("Content-Type", "application/json")
                        .withHeader("Retry-After", "60")
                        .withBody("""
                            {
                                "type": "error",
                                "error": {
                                    "message": "Rate limit exceeded"
                                }
                            }
                            """)));
    }

    public void setupCommitsResponse(String workspaceSlug, String repoSlug) {
        server.stubFor(get(urlPathMatching("/2.0/repositories/" + workspaceSlug + "/" + repoSlug + "/commits.*"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("""
                            {
                                "values": [
                                    {
                                        "hash": "abc123def456",
                                        "date": "2024-01-15T10:30:00+00:00",
                                        "message": "Initial commit",
                                        "author": {
                                            "raw": "Test User <test@example.com>"
                                        }
                                    }
                                ],
                                "page": 1,
                                "pagelen": 30
                            }
                            """)));
    }

    public void setupFileContent(String workspaceSlug, String repoSlug, String branch, String filePath) {
        server.stubFor(get(urlPathMatching("/2.0/repositories/" + workspaceSlug + "/" + repoSlug + "/src/" + branch + "/" + filePath + ".*"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "text/plain")
                        .withBody("""
                            public class Main {
                                public static void main(String[] args) {
                                    System.out.println("Hello World");
                                }
                            }
                            """)));
    }

    public void setupPullRequestDetails(String workspaceSlug, String repoSlug, int prId) {
        setupPullRequestResponse(workspaceSlug, repoSlug, prId);
    }

    public void setupPullRequestDiff(String workspaceSlug, String repoSlug, int prId) {
        server.stubFor(get(urlPathEqualTo("/2.0/repositories/" + workspaceSlug + "/" + repoSlug + "/pullrequests/" + prId + "/diff"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "text/plain")
                        .withBody("""
                            diff --git a/src/Main.java b/src/Main.java
                            index abc123..def456 100644
                            --- a/src/Main.java
                            +++ b/src/Main.java
                            @@ -1,5 +1,7 @@
                             public class Main {
                            +    // New feature
                                 public static void main(String[] args) {
                            +        System.out.println("Hello, World!");
                                 }
                             }
                            """)));
    }

    public void setupPullRequestCommentCreation(String workspaceSlug, String repoSlug, int prId) {
        server.stubFor(post(urlPathEqualTo("/2.0/repositories/" + workspaceSlug + "/" + repoSlug + "/pullrequests/" + prId + "/comments"))
                .willReturn(aResponse()
                        .withStatus(201)
                        .withHeader("Content-Type", "application/json")
                        .withBody("""
                            {
                                "id": 12345,
                                "content": {
                                    "raw": "Code review comment"
                                },
                                "created_on": "2024-01-15T10:30:00+00:00"
                            }
                            """)));
    }

    public void setupBranchCommitsResponse(String workspaceSlug, String repoSlug, String branch) {
        server.stubFor(get(urlPathMatching("/2.0/repositories/" + workspaceSlug + "/" + repoSlug + "/commits/" + branch + ".*"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("""
                            {
                                "values": [
                                    {
                                        "hash": "abc123",
                                        "date": "2024-01-15T10:30:00+00:00",
                                        "message": "Feature commit 1"
                                    },
                                    {
                                        "hash": "def456",
                                        "date": "2024-01-14T10:30:00+00:00",
                                        "message": "Feature commit 2"
                                    }
                                ],
                                "page": 1,
                                "pagelen": 30
                            }
                            """)));
    }

    public void setupCommitDiff(String workspaceSlug, String repoSlug, String commitHash) {
        server.stubFor(get(urlPathEqualTo("/2.0/repositories/" + workspaceSlug + "/" + repoSlug + "/diff/" + commitHash))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "text/plain")
                        .withBody("""
                            diff --git a/src/Service.java b/src/Service.java
                            index 123..456 100644
                            --- a/src/Service.java
                            +++ b/src/Service.java
                            @@ -10,3 +10,5 @@
                             public class Service {
                            +    public void newMethod() {
                            +    }
                             }
                            """)));
    }

    public void setupBranchCompare(String workspaceSlug, String repoSlug, String sourceBranch, String targetBranch) {
        server.stubFor(get(urlPathEqualTo("/2.0/repositories/" + workspaceSlug + "/" + repoSlug + "/diff/" + sourceBranch + ".." + targetBranch))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "text/plain")
                        .withBody("""
                            diff --git a/src/Main.java b/src/Main.java
                            index abc..def 100644
                            --- a/src/Main.java
                            +++ b/src/Main.java
                            @@ -1,5 +1,10 @@
                             public class Main {
                            +    // Branch differences
                             }
                            """)));
    }

    public void setupBranchFileTree(String workspaceSlug, String repoSlug, String branch) {
        server.stubFor(get(urlPathMatching("/2.0/repositories/" + workspaceSlug + "/" + repoSlug + "/src/" + branch + "/.*"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("""
                            {
                                "values": [
                                    {
                                        "path": "src/Main.java",
                                        "type": "commit_file",
                                        "size": 256
                                    },
                                    {
                                        "path": "src/Service.java",
                                        "type": "commit_file",
                                        "size": 512
                                    }
                                ]
                            }
                            """)));
    }

    public void setupCommitChangedFiles(String workspaceSlug, String repoSlug, String commitHash) {
        server.stubFor(get(urlPathEqualTo("/2.0/repositories/" + workspaceSlug + "/" + repoSlug + "/diffstat/" + commitHash))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("""
                            {
                                "values": [
                                    {
                                        "status": "modified",
                                        "old": {"path": "src/Service.java"},
                                        "new": {"path": "src/Service.java"}
                                    }
                                ]
                            }
                            """)));
    }
}
