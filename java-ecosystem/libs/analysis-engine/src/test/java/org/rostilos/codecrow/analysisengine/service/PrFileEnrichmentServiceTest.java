package org.rostilos.codecrow.analysisengine.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.*;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.springframework.test.util.ReflectionTestUtils;

import java.io.IOException;
import java.util.*;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
@DisplayName("PrFileEnrichmentService")
class PrFileEnrichmentServiceTest {

    @Mock private VcsClient vcsClient;

    private PrFileEnrichmentService service;
    private MockWebServer mockWebServer;
    private final ObjectMapper objectMapper = new ObjectMapper();

    @BeforeEach
    void setUp() throws IOException {
        mockWebServer = new MockWebServer();
        mockWebServer.start();
        service = new PrFileEnrichmentService();
        String baseUrl = mockWebServer.url("/").toString();
        if (baseUrl.endsWith("/")) {
            baseUrl = baseUrl.substring(0, baseUrl.length() - 1);
        }
        ReflectionTestUtils.setField(service, "enrichmentEnabled", true);
        ReflectionTestUtils.setField(service, "maxFileSizeBytes", 102400L);
        ReflectionTestUtils.setField(service, "maxTotalSizeBytes", 10485760L);
        ReflectionTestUtils.setField(service, "ragPipelineUrl", baseUrl);
        ReflectionTestUtils.setField(service, "requestTimeoutSeconds", 5);
    }

    @AfterEach
    void tearDown() throws IOException {
        mockWebServer.shutdown();
    }

    @Nested
    @DisplayName("isEnrichmentEnabled()")
    class IsEnabledTests {
        @Test void returnsTrue_whenEnabled() {
            assertThat(service.isEnrichmentEnabled()).isTrue();
        }

        @Test void returnsFalse_whenDisabled() {
            ReflectionTestUtils.setField(service, "enrichmentEnabled", false);
            assertThat(service.isEnrichmentEnabled()).isFalse();
        }
    }

    @Nested
    @DisplayName("enrichPrFiles() - disabled / empty input")
    class DisabledAndEmptyTests {
        @Test void returnsEmpty_whenDisabled() {
            ReflectionTestUtils.setField(service, "enrichmentEnabled", false);
            PrEnrichmentDataDto result = service.enrichPrFiles(vcsClient, "ws", "repo", "main", List.of("a.java"));
            assertThat(result.hasData()).isFalse();
        }

        @Test void returnsEmpty_whenNullFiles() {
            PrEnrichmentDataDto result = service.enrichPrFiles(vcsClient, "ws", "repo", "main", null);
            assertThat(result.hasData()).isFalse();
        }

        @Test void returnsEmpty_whenEmptyFiles() {
            PrEnrichmentDataDto result = service.enrichPrFiles(vcsClient, "ws", "repo", "main", List.of());
            assertThat(result.hasData()).isFalse();
        }
    }

    @Nested
    @DisplayName("enrichPrFiles() - file filtering")
    class FilterTests {
        @Test void filtersUnsupportedExtensions() {
            PrEnrichmentDataDto result = service.enrichPrFiles(vcsClient, "ws", "repo", "main",
                    List.of("README.md", "image.png", "data.json", "config.yml"));
            assertThat(result.stats().filesSkipped()).isEqualTo(4);
            assertThat(result.stats().skipReasons()).containsKey("unsupported_extension");
        }

        @Test void keepsSupportedExtensions() throws Exception {
            List<String> files = List.of("Main.java", "app.py", "index.ts", "style.css");
            Map<String, String> contents = Map.of(
                    "Main.java", "public class Main {}",
                    "app.py", "print('hello')",
                    "index.ts", "export default {}"
            );
            when(vcsClient.getFileContents(eq("ws"), eq("repo"), anyList(), eq("main"), anyInt()))
                    .thenReturn(contents);

            mockWebServer.enqueue(new MockResponse()
                    .setResponseCode(200)
                    .addHeader("Content-Type", "application/json")
                    .setBody("{\"results\":[], \"total_files\":3, \"successful\":3, \"failed\":0}"));

            PrEnrichmentDataDto result = service.enrichPrFiles(vcsClient, "ws", "repo", "main", files);
            assertThat(result.stats().totalFilesRequested()).isEqualTo(4);
            assertThat(result.stats().skipReasons()).containsKey("unsupported_extension");
        }
    }

    @Nested
    @DisplayName("enrichPrFiles() - successful flow")
    class SuccessFlowTests {

        @Test void successfulEnrichment_withRelationships() throws Exception {
            List<String> files = List.of("src/Service.java", "src/Repository.java");
            Map<String, String> contents = Map.of(
                    "src/Service.java", "import Repository; public class Service extends Base {}",
                    "src/Repository.java", "public class Repository {}"
            );
            when(vcsClient.getFileContents(eq("ws"), eq("repo"), anyList(), eq("main"), anyInt()))
                    .thenReturn(contents);

            String parseResponse = objectMapper.writeValueAsString(Map.of(
                    "results", List.of(
                            Map.of("path", "src/Service.java", "language", "java",
                                    "imports", List.of("Repository"),
                                    "extends", List.of("Base"),
                                    "implements", List.of(),
                                    "semantic_names", List.of("Service"),
                                    "calls", List.of()),
                            Map.of("path", "src/Repository.java", "language", "java",
                                    "imports", List.of(),
                                    "extends", List.of(),
                                    "implements", List.of(),
                                    "semantic_names", List.of("Repository"),
                                    "calls", List.of())
                    ),
                    "total_files", 2, "successful", 2, "failed", 0));
            mockWebServer.enqueue(new MockResponse()
                    .setResponseCode(200)
                    .addHeader("Content-Type", "application/json")
                    .setBody(parseResponse));

            PrEnrichmentDataDto result = service.enrichPrFiles(vcsClient, "ws", "repo", "main", files);

            assertThat(result.hasData()).isTrue();
            assertThat(result.fileContents()).hasSize(2);
            assertThat(result.stats().filesEnriched()).isEqualTo(2);
            assertThat(result.stats().processingTimeMs()).isGreaterThanOrEqualTo(0);
            assertThat(result.relationships()).isNotEmpty();
        }

        @Test void sendsParseBatchRequest() throws Exception {
            List<String> files = List.of("App.java");
            when(vcsClient.getFileContents(eq("ws"), eq("repo"), anyList(), eq("main"), anyInt()))
                    .thenReturn(Map.of("App.java", "public class App {}"));

            mockWebServer.enqueue(new MockResponse()
                    .setResponseCode(200)
                    .addHeader("Content-Type", "application/json")
                    .setBody("{\"results\":[], \"total_files\":1}"));

            service.enrichPrFiles(vcsClient, "ws", "repo", "main", files);

            RecordedRequest recorded = mockWebServer.takeRequest();
            assertThat(recorded.getPath()).endsWith("/parse/batch");
            assertThat(recorded.getMethod()).isEqualTo("POST");
            String body = recorded.getBody().readUtf8();
            assertThat(body).contains("App.java").contains("public class App {}");
        }
    }

    @Nested
    @DisplayName("enrichPrFiles() - file content issues")
    class FileContentIssueTests {
        @Test void handlesNullContent_fetchFailed() throws Exception {
            List<String> files = List.of("Missing.java");
            when(vcsClient.getFileContents(eq("ws"), eq("repo"), anyList(), eq("main"), anyInt()))
                    .thenReturn(Map.of());

            PrEnrichmentDataDto result = service.enrichPrFiles(vcsClient, "ws", "repo", "main", files);
            assertThat(result.stats().skipReasons()).containsKey("fetch_failed");
        }

        @Test void handlesEmptyFileContent() throws Exception {
            List<String> files = List.of("Empty.java");
            when(vcsClient.getFileContents(eq("ws"), eq("repo"), anyList(), eq("main"), anyInt()))
                    .thenReturn(Map.of("Empty.java", ""));

            PrEnrichmentDataDto result = service.enrichPrFiles(vcsClient, "ws", "repo", "main", files);
            assertThat(result.stats().skipReasons()).containsKey("empty_file");
        }
    }

    @Nested
    @DisplayName("enrichPrFiles() - size limits")
    class SizeLimitTests {
        @Test void truncatesWhenTotalSizeExceeded() throws Exception {
            ReflectionTestUtils.setField(service, "maxTotalSizeBytes", 50L);

            List<String> files = List.of("big1.java", "big2.java");
            when(vcsClient.getFileContents(eq("ws"), eq("repo"), anyList(), eq("main"), anyInt()))
                    .thenReturn(Map.of(
                            "big1.java", "a".repeat(30),
                            "big2.java", "b".repeat(30)));

            mockWebServer.enqueue(new MockResponse()
                    .setResponseCode(200)
                    .addHeader("Content-Type", "application/json")
                    .setBody("{\"results\":[]}"));

            PrEnrichmentDataDto result = service.enrichPrFiles(vcsClient, "ws", "repo", "main", files);
            assertThat(result.stats().skipReasons()).containsKey("total_size_limit");
        }
    }

    @Nested
    @DisplayName("enrichPrFiles() - RAG parse failures")
    class ParseFailureTests {
        @Test void fallbackMetadata_whenParseReturns500() throws Exception {
            List<String> files = List.of("Fail.java");
            when(vcsClient.getFileContents(eq("ws"), eq("repo"), anyList(), eq("main"), anyInt()))
                    .thenReturn(Map.of("Fail.java", "class Fail {}"));

            mockWebServer.enqueue(new MockResponse().setResponseCode(500));

            PrEnrichmentDataDto result = service.enrichPrFiles(vcsClient, "ws", "repo", "main", files);
            assertThat(result.fileContents()).hasSize(1);
            assertThat(result.fileMetadata()).isNotEmpty();
            assertThat(result.fileMetadata().get(0).error()).isEqualTo("parse_failed");
        }

        @Test void fallbackMetadata_whenParseReturnsNullResults() throws Exception {
            List<String> files = List.of("NullBody.java");
            when(vcsClient.getFileContents(eq("ws"), eq("repo"), anyList(), eq("main"), anyInt()))
                    .thenReturn(Map.of("NullBody.java", "class NullBody {}"));

            mockWebServer.enqueue(new MockResponse()
                    .setResponseCode(200)
                    .addHeader("Content-Type", "application/json")
                    .setBody("{\"results\":null}"));

            PrEnrichmentDataDto result = service.enrichPrFiles(vcsClient, "ws", "repo", "main", files);
            assertThat(result.fileMetadata()).isNotEmpty();
            assertThat(result.fileMetadata().get(0).error()).isEqualTo("parse_failed");
        }
    }

    @Nested
    @DisplayName("enrichPrFiles() - exception handling")
    class ExceptionTests {
        @Test void returnsEmptyWithStats_onException() throws Exception {
            List<String> files = List.of("Error.java");
            when(vcsClient.getFileContents(eq("ws"), eq("repo"), anyList(), eq("main"), anyInt()))
                    .thenThrow(new RuntimeException("VCS down"));

            PrEnrichmentDataDto result = service.enrichPrFiles(vcsClient, "ws", "repo", "main", files);
            assertThat(result.hasData()).isFalse();
            assertThat(result.stats().totalFilesRequested()).isEqualTo(1);
            assertThat(result.stats().skipReasons()).containsKey("error");
        }
    }

    @Nested
    @DisplayName("enrichPrFiles() - relationship building")
    class RelationshipTests {
        @Test void buildsSamePackageRelationships() throws Exception {
            List<String> files = List.of("com/example/A.java", "com/example/B.java", "com/other/C.java");
            Map<String, String> contents = Map.of(
                    "com/example/A.java", "class A {}",
                    "com/example/B.java", "class B {}",
                    "com/other/C.java", "class C {}");
            when(vcsClient.getFileContents(eq("ws"), eq("repo"), anyList(), eq("main"), anyInt()))
                    .thenReturn(contents);

            mockWebServer.enqueue(new MockResponse()
                    .setResponseCode(200)
                    .addHeader("Content-Type", "application/json")
                    .setBody("{\"results\":[]}"));

            PrEnrichmentDataDto result = service.enrichPrFiles(vcsClient, "ws", "repo", "main", files);
            assertThat(result.relationships().stream()
                    .anyMatch(r -> r.relationshipType() == FileRelationshipDto.RelationshipType.SAME_PACKAGE))
                    .isTrue();
        }

        @Test void buildsImportRelationships() throws Exception {
            List<String> files = List.of("src/UserService.java", "src/UserRepo.java");
            when(vcsClient.getFileContents(eq("ws"), eq("repo"), anyList(), eq("main"), anyInt()))
                    .thenReturn(Map.of(
                            "src/UserService.java", "import UserRepo;",
                            "src/UserRepo.java", "class UserRepo {}"));

            String parseResponse = objectMapper.writeValueAsString(Map.of("results", List.of(
                    Map.of("path", "src/UserService.java", "imports", List.of("UserRepo"),
                            "extends", List.of(), "implements", List.of(), "calls", List.of()),
                    Map.of("path", "src/UserRepo.java", "imports", List.of(),
                            "extends", List.of(), "implements", List.of(), "calls", List.of()))));
            mockWebServer.enqueue(new MockResponse()
                    .setResponseCode(200)
                    .addHeader("Content-Type", "application/json")
                    .setBody(parseResponse));

            PrEnrichmentDataDto result = service.enrichPrFiles(vcsClient, "ws", "repo", "main", files);
            assertThat(result.relationships().stream()
                    .anyMatch(r -> r.relationshipType() == FileRelationshipDto.RelationshipType.IMPORTS
                            && r.sourceFile().equals("src/UserService.java")
                            && r.targetFile().equals("src/UserRepo.java")))
                    .isTrue();
        }

        @Test void doesNotCreateSelfRelationships_excludesSameFile() throws Exception {
            List<String> files = List.of("src/Self.java");
            when(vcsClient.getFileContents(eq("ws"), eq("repo"), anyList(), eq("main"), anyInt()))
                    .thenReturn(Map.of("src/Self.java", "import Self;"));

            mockWebServer.enqueue(new MockResponse()
                    .setResponseCode(200)
                    .addHeader("Content-Type", "application/json")
                    .setBody(objectMapper.writeValueAsString(Map.of("results", List.of(
                            Map.of("path", "src/Self.java", "imports", List.of("Self"),
                                    "extends", List.of(), "implements", List.of(), "calls", List.of()))))));

            PrEnrichmentDataDto result = service.enrichPrFiles(vcsClient, "ws", "repo", "main", files);
            assertThat(result.relationships().stream()
                    .noneMatch(r -> r.sourceFile().equals(r.targetFile())))
                    .isTrue();
        }

        @Test void buildsExtendsRelationships() throws Exception {
            List<String> files = List.of("src/Child.java", "src/Parent.java");
            when(vcsClient.getFileContents(eq("ws"), eq("repo"), anyList(), eq("main"), anyInt()))
                    .thenReturn(Map.of("src/Child.java", "class Child extends Parent {}", "src/Parent.java", "class Parent {}"));

            mockWebServer.enqueue(new MockResponse()
                    .setResponseCode(200)
                    .addHeader("Content-Type", "application/json")
                    .setBody(objectMapper.writeValueAsString(Map.of("results", List.of(
                            Map.of("path", "src/Child.java", "imports", List.of(),
                                    "extends", List.of("Parent"), "implements", List.of(), "calls", List.of()),
                            Map.of("path", "src/Parent.java", "imports", List.of(),
                                    "extends", List.of(), "implements", List.of(), "calls", List.of()))))));

            PrEnrichmentDataDto result = service.enrichPrFiles(vcsClient, "ws", "repo", "main", files);
            assertThat(result.relationships().stream()
                    .anyMatch(r -> r.relationshipType() == FileRelationshipDto.RelationshipType.EXTENDS))
                    .isTrue();
        }

        @Test void buildsImplementsRelationships() throws Exception {
            List<String> files = List.of("src/Impl.java", "src/Api.java");
            when(vcsClient.getFileContents(eq("ws"), eq("repo"), anyList(), eq("main"), anyInt()))
                    .thenReturn(Map.of("src/Impl.java", "class Impl implements Api {}", "src/Api.java", "interface Api {}"));

            mockWebServer.enqueue(new MockResponse()
                    .setResponseCode(200)
                    .addHeader("Content-Type", "application/json")
                    .setBody(objectMapper.writeValueAsString(Map.of("results", List.of(
                            Map.of("path", "src/Impl.java", "imports", List.of(),
                                    "extends", List.of(), "implements", List.of("Api"), "calls", List.of()),
                            Map.of("path", "src/Api.java", "imports", List.of(),
                                    "extends", List.of(), "implements", List.of(), "calls", List.of()))))));

            PrEnrichmentDataDto result = service.enrichPrFiles(vcsClient, "ws", "repo", "main", files);
            assertThat(result.relationships().stream()
                    .anyMatch(r -> r.relationshipType() == FileRelationshipDto.RelationshipType.IMPLEMENTS))
                    .isTrue();
        }

        @Test void buildsCallsRelationships() throws Exception {
            List<String> files = List.of("src/Caller.java", "src/Callee.java");
            when(vcsClient.getFileContents(eq("ws"), eq("repo"), anyList(), eq("main"), anyInt()))
                    .thenReturn(Map.of("src/Caller.java", "class Caller {}", "src/Callee.java", "class Callee {}"));

            mockWebServer.enqueue(new MockResponse()
                    .setResponseCode(200)
                    .addHeader("Content-Type", "application/json")
                    .setBody(objectMapper.writeValueAsString(Map.of("results", List.of(
                            Map.of("path", "src/Caller.java", "imports", List.of(),
                                    "extends", List.of(), "implements", List.of(), "calls", List.of("Callee")),
                            Map.of("path", "src/Callee.java", "imports", List.of(),
                                    "extends", List.of(), "implements", List.of(), "calls", List.of()))))));

            PrEnrichmentDataDto result = service.enrichPrFiles(vcsClient, "ws", "repo", "main", files);
            assertThat(result.relationships().stream()
                    .anyMatch(r -> r.relationshipType() == FileRelationshipDto.RelationshipType.CALLS))
                    .isTrue();
        }
    }

    @Nested
    @DisplayName("Supported file extensions")
    class ExtensionTests {
        @Test void supportsAllDeclaredExtensions() throws Exception {
            List<String> files = List.of(
                    "App.java", "main.py", "index.js", "app.ts", "comp.jsx", "comp.tsx",
                    "main.go", "lib.rs", "gem.rb", "web.php", "Program.cs",
                    "main.cpp", "util.c", "util.h", "Main.kt", "App.scala",
                    "VC.swift", "VC.m", "VC.mm");
            Map<String, String> contents = new HashMap<>();
            for (String f : files) contents.put(f, "content of " + f);

            when(vcsClient.getFileContents(eq("ws"), eq("repo"), anyList(), eq("main"), anyInt()))
                    .thenReturn(contents);

            mockWebServer.enqueue(new MockResponse()
                    .setResponseCode(200)
                    .addHeader("Content-Type", "application/json")
                    .setBody("{\"results\":[]}"));

            PrEnrichmentDataDto result = service.enrichPrFiles(vcsClient, "ws", "repo", "main", files);
            assertThat(result.stats().filesEnriched()).isEqualTo(files.size());
            assertThat(result.stats().skipReasons()).doesNotContainKey("unsupported_extension");
        }
    }
}
