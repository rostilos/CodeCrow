package org.rostilos.codecrow.integration.mock;

import com.github.tomakehurst.wiremock.WireMockServer;

import static com.github.tomakehurst.wiremock.client.WireMock.*;

/**
 * Helper class for setting up WireMock stubs for RAG Pipeline API.
 */
public class RagPipelineMockSetup {

    private final WireMockServer server;

    public RagPipelineMockSetup(WireMockServer server) {
        this.server = server;
    }

    public void setupHealthCheck() {
        server.stubFor(get(urlPathEqualTo("/health"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("""
                            {
                                "status": "healthy",
                                "version": "1.0.0"
                            }
                            """)));
    }

    public void setupIndexRepository(String workspace, String project, int filesIndexed) {
        server.stubFor(post(urlPathEqualTo("/api/index"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody(String.format("""
                            {
                                "status": "success",
                                "workspace": "%s",
                                "project": "%s",
                                "files_indexed": %d,
                                "index_time_ms": 1234,
                                "collection_name": "%s_%s"
                            }
                            """, workspace, project, filesIndexed, workspace, project))));
    }

    public void setupIncrementalUpdate(String workspace, String project, int filesUpdated, int filesDeleted) {
        server.stubFor(post(urlPathEqualTo("/api/update"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody(String.format("""
                            {
                                "status": "success",
                                "workspace": "%s",
                                "project": "%s",
                                "files_updated": %d,
                                "files_deleted": %d,
                                "update_time_ms": 567
                            }
                            """, workspace, project, filesUpdated, filesDeleted))));
    }

    public void setupDeleteFiles(String workspace, String project) {
        server.stubFor(delete(urlPathEqualTo("/api/files"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody(String.format("""
                            {
                                "status": "success",
                                "workspace": "%s",
                                "project": "%s",
                                "files_deleted": 5
                            }
                            """, workspace, project))));
    }

    public void setupQuery(String query, String[] results) {
        StringBuilder resultsJson = new StringBuilder("[");
        for (int i = 0; i < results.length; i++) {
            if (i > 0) resultsJson.append(",");
            resultsJson.append(String.format("""
                {
                    "content": "%s",
                    "file_path": "src/example%d.java",
                    "score": %.2f,
                    "metadata": {
                        "language": "java",
                        "lines": [1, 50]
                    }
                }
                """, escapeJson(results[i]), i, 0.95 - (i * 0.1)));
        }
        resultsJson.append("]");

        server.stubFor(post(urlPathEqualTo("/api/query"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody(String.format("""
                            {
                                "status": "success",
                                "query": "%s",
                                "results": %s,
                                "query_time_ms": 45
                            }
                            """, escapeJson(query), resultsJson.toString()))));
    }

    public void setupGetIndexStatus(String workspace, String project, String status, int totalFiles) {
        server.stubFor(get(urlPathEqualTo("/api/status/" + workspace + "/" + project))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody(String.format("""
                            {
                                "workspace": "%s",
                                "project": "%s",
                                "status": "%s",
                                "total_files": %d,
                                "last_indexed_at": "2024-01-15T10:30:00Z",
                                "collection_name": "%s_%s"
                            }
                            """, workspace, project, status, totalFiles, workspace, project))));
    }

    public void setupDeleteCollection(String workspace, String project) {
        server.stubFor(delete(urlPathEqualTo("/api/collection/" + workspace + "/" + project))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody(String.format("""
                            {
                                "status": "success",
                                "message": "Collection deleted",
                                "workspace": "%s",
                                "project": "%s"
                            }
                            """, workspace, project))));
    }

    public void setupRelevanceTestQuery(String query, double expectedScore, String[] relevantContent) {
        StringBuilder resultsJson = new StringBuilder("[");
        for (int i = 0; i < relevantContent.length; i++) {
            if (i > 0) resultsJson.append(",");
            double score = expectedScore - (i * 0.05);
            resultsJson.append(String.format("""
                {
                    "content": "%s",
                    "file_path": "src/relevant%d.java",
                    "score": %.4f,
                    "metadata": {
                        "language": "java",
                        "function_name": "relevantFunction%d"
                    }
                }
                """, escapeJson(relevantContent[i]), i, score, i));
        }
        resultsJson.append("]");

        server.stubFor(post(urlPathEqualTo("/api/query"))
                .withRequestBody(containing(query))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody(String.format("""
                            {
                                "status": "success",
                                "query": "%s",
                                "results": %s,
                                "query_time_ms": 32,
                                "relevance_info": {
                                    "avg_score": %.4f,
                                    "top_score": %.4f
                                }
                            }
                            """, escapeJson(query), resultsJson.toString(), 
                            expectedScore - 0.1, expectedScore))));
    }

    public void setupServiceUnavailable() {
        server.stubFor(any(anyUrl())
                .willReturn(aResponse()
                        .withStatus(503)
                        .withHeader("Content-Type", "application/json")
                        .withBody("""
                            {
                                "status": "error",
                                "message": "Service temporarily unavailable"
                            }
                            """)));
    }

    public void setupIndexingInProgress() {
        server.stubFor(post(urlPathEqualTo("/api/index"))
                .willReturn(aResponse()
                        .withStatus(409)
                        .withHeader("Content-Type", "application/json")
                        .withBody("""
                            {
                                "status": "conflict",
                                "message": "Indexing already in progress"
                            }
                            """)));
    }

    private String escapeJson(String text) {
        return text
                .replace("\\", "\\\\")
                .replace("\"", "\\\"")
                .replace("\n", "\\n")
                .replace("\r", "\\r")
                .replace("\t", "\\t");
    }

    public void setupIndexingSuccess(String projectNamespace) {
        server.stubFor(post(urlPathMatching("/api/index.*"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody(String.format("""
                            {
                                "status": "success",
                                "project": "%s",
                                "files_indexed": 50,
                                "index_time_ms": 2500
                            }
                            """, projectNamespace))));
    }

    public void setupUpdateSuccess(String projectNamespace) {
        server.stubFor(post(urlPathMatching("/api/update.*"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody(String.format("""
                            {
                                "status": "success",
                                "project": "%s",
                                "files_updated": 10,
                                "files_deleted": 2
                            }
                            """, projectNamespace))));
    }

    public void setupDeleteSuccess(String projectNamespace) {
        server.stubFor(delete(urlPathMatching("/api/.*"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody(String.format("""
                            {
                                "status": "success",
                                "project": "%s",
                                "message": "Collection deleted successfully"
                            }
                            """, projectNamespace))));
    }

    public void setupSearchResults(String projectNamespace, String query) {
        server.stubFor(post(urlPathMatching("/api/query.*"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody(String.format("""
                            {
                                "status": "success",
                                "project": "%s",
                                "query": "%s",
                                "results": [
                                    {
                                        "content": "public class UserService implements Authentication { ... }",
                                        "file_path": "src/main/java/service/UserService.java",
                                        "score": 0.95,
                                        "metadata": {
                                            "language": "java",
                                            "lines": [1, 100]
                                        }
                                    },
                                    {
                                        "content": "public interface Authentication { ... }",
                                        "file_path": "src/main/java/auth/Authentication.java",
                                        "score": 0.88,
                                        "metadata": {
                                            "language": "java",
                                            "lines": [1, 25]
                                        }
                                    }
                                ],
                                "query_time_ms": 45
                            }
                            """, projectNamespace, escapeJson(query)))));
    }

    public void setupGetIndexStatus(String projectNamespace, String status, int totalFiles) {
        server.stubFor(get(urlPathMatching("/api/status/.*"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody(String.format("""
                            {
                                "project": "%s",
                                "status": "%s",
                                "total_files": %d,
                                "last_indexed_at": "2024-01-15T10:30:00Z"
                            }
                            """, projectNamespace, status, totalFiles))));
    }
}
