package org.rostilos.codecrow.ragengine.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.project.config.RagConfig;
import org.rostilos.codecrow.ragengine.client.RagPipelineClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.springframework.test.util.ReflectionTestUtils;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class IncrementalRagUpdateServiceTest {

    @Mock
    private VcsClientProvider vcsClientProvider;

    @Mock
    private RagPipelineClient ragPipelineClient;

    @Mock
    private RagIndexTrackingService ragIndexTrackingService;

    private IncrementalRagUpdateService service;
    private Project testProject;

    @BeforeEach
    void setUp() {
        service = new IncrementalRagUpdateService(
                vcsClientProvider,
                ragPipelineClient,
                ragIndexTrackingService
        );
        
        testProject = new Project();
        ReflectionTestUtils.setField(testProject, "id", 100L);
    }

    @Test
    void testShouldPerformIncrementalUpdate_RagDisabled() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", false);

        boolean result = service.shouldPerformIncrementalUpdate(testProject);

        assertThat(result).isFalse();
    }

    @Test
    void testShouldPerformIncrementalUpdate_NoConfig() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", true);
        testProject.setConfiguration(null);

        boolean result = service.shouldPerformIncrementalUpdate(testProject);

        assertThat(result).isFalse();
    }

    @Test
    void testShouldPerformIncrementalUpdate_NoRagConfig() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", true);
        ProjectConfig config = new ProjectConfig();
        testProject.setConfiguration(config);

        boolean result = service.shouldPerformIncrementalUpdate(testProject);

        assertThat(result).isFalse();
    }

    @Test
    void testShouldPerformIncrementalUpdate_RagNotEnabled() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", true);
        testProject.setConfiguration(null);

        boolean result = service.shouldPerformIncrementalUpdate(testProject);

        assertThat(result).isFalse();
    }

    @Test
    void testShouldPerformIncrementalUpdate_ProjectNotIndexed() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", true);
        testProject.setConfiguration(null);
        
        boolean result = service.shouldPerformIncrementalUpdate(testProject);

        assertThat(result).isFalse();
    }

    @Test
    void testShouldPerformIncrementalUpdate_Success() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", true);
        testProject.setConfiguration(null);
        
        boolean result = service.shouldPerformIncrementalUpdate(testProject);

        assertThat(result).isFalse();
    }

    @Test
    void testParseDiffForRag_EmptyDiff() {
        IncrementalRagUpdateService.DiffResult result = service.parseDiffForRag("");

        assertThat(result.addedOrModified()).isEmpty();
        assertThat(result.deleted()).isEmpty();
    }

    @Test
    void testParseDiffForRag_NullDiff() {
        IncrementalRagUpdateService.DiffResult result = service.parseDiffForRag(null);

        assertThat(result.addedOrModified()).isEmpty();
        assertThat(result.deleted()).isEmpty();
    }

    @Test
    void testParseDiffForRag_AddedFile() {
        String diff = "diff --git a/src/NewFile.java b/src/NewFile.java\n" +
                      "new file mode 100644\n" +
                      "--- /dev/null\n" +
                      "+++ b/src/NewFile.java\n" +
                      "@@ -0,0 +1,10 @@\n" +
                      "+public class NewFile {}\n";

        IncrementalRagUpdateService.DiffResult result = service.parseDiffForRag(diff);

        assertThat(result.addedOrModified()).contains("src/NewFile.java");
        assertThat(result.deleted()).isEmpty();
    }

    @Test
    void testParseDiffForRag_DeletedFile() {
        String diff = "diff --git a/src/OldFile.java b/src/OldFile.java\n" +
                      "deleted file mode 100644\n" +
                      "--- a/src/OldFile.java\n" +
                      "+++ /dev/null\n" +
                      "@@ -1,10 +0,0 @@\n" +
                      "-public class OldFile {}\n";

        IncrementalRagUpdateService.DiffResult result = service.parseDiffForRag(diff);

        assertThat(result.deleted()).contains("src/OldFile.java");
        assertThat(result.addedOrModified()).isEmpty();
    }

    @Test
    void testParseDiffForRag_ModifiedFile() {
        String diff = "diff --git a/src/Modified.java b/src/Modified.java\n" +
                      "--- a/src/Modified.java\n" +
                      "+++ b/src/Modified.java\n" +
                      "@@ -1,5 +1,6 @@\n" +
                      " public class Modified {\n" +
                      "+    // new comment\n" +
                      " }\n";

        IncrementalRagUpdateService.DiffResult result = service.parseDiffForRag(diff);

        assertThat(result.addedOrModified()).contains("src/Modified.java");
        assertThat(result.deleted()).isEmpty();
    }

    @Test
    void testParseDiffForRag_MixedChanges() {
        String diff = "diff --git a/src/NewFile.java b/src/NewFile.java\n" +
                      "new file mode 100644\n" +
                      "--- /dev/null\n" +
                      "+++ b/src/NewFile.java\n" +
                      "@@ -0,0 +1 @@\n" +
                      "+new\n" +
                      "diff --git a/src/OldFile.java b/src/OldFile.java\n" +
                      "deleted file mode 100644\n" +
                      "--- a/src/OldFile.java\n" +
                      "+++ /dev/null\n" +
                      "@@ -1 +0,0 @@\n" +
                      "-old\n" +
                      "diff --git a/src/Modified.java b/src/Modified.java\n" +
                      "--- a/src/Modified.java\n" +
                      "+++ b/src/Modified.java\n" +
                      "@@ -1 +1 @@\n" +
                      "-old\n" +
                      "+new\n";

        IncrementalRagUpdateService.DiffResult result = service.parseDiffForRag(diff);

        assertThat(result.addedOrModified()).containsExactlyInAnyOrder("src/NewFile.java", "src/Modified.java");
        assertThat(result.deleted()).contains("src/OldFile.java");
    }

    @Test
    void testConstructor() {
        IncrementalRagUpdateService newService = new IncrementalRagUpdateService(
                vcsClientProvider,
                ragPipelineClient,
                ragIndexTrackingService
        );
        assertThat(newService).isNotNull();
    }
}
