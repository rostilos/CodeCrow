package org.rostilos.codecrow.core.persistence;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.persistence.EntityManager;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;
import org.rostilos.codecrow.core.service.PrChangeSummaryService;
import org.rostilos.codecrow.testsupport.annotation.IntegrationTest;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.SpringBootConfiguration;
import org.springframework.boot.autoconfigure.EnableAutoConfiguration;
import org.springframework.boot.autoconfigure.domain.EntityScan;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.data.jpa.repository.config.EnableJpaRepositories;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;

/** Real committed PostgreSQL roundtrips, optionally using an actual benchmark corpus. */
@IntegrationTest
@SpringBootTest(classes = BenchmarkChangeSummaryIT.Config.class,
        properties = {"spring.flyway.enabled=false", "spring.jpa.hibernate.ddl-auto=create-drop"})
class BenchmarkChangeSummaryIT {
    @SpringBootConfiguration @EnableAutoConfiguration
    @EntityScan("org.rostilos.codecrow.core.model")
    @EnableJpaRepositories(basePackageClasses = CodeAnalysisRepository.class)
    static class Config {}

    @Autowired EntityManager em;
    @Autowired PlatformTransactionManager transactions;
    @Autowired CodeAnalysisRepository repository;
    private final ObjectMapper mapper = new ObjectMapper();

    @Test void committedApplicationRoundtripPreservesWholeGraphAndTenantIdentity() throws Exception {
        var inputs = new LinkedHashMap<String, Map<String, Object>>();
        String directory = System.getenv("CODECROW_BENCHMARK_RESPONSES_DIR");
        if (directory != null && !directory.isBlank()) {
            try (var paths = Files.list(Path.of(directory))) {
                for (Path path : paths.filter(p -> p.toString().endsWith(".json")).sorted().toList()) {
                    var tree = mapper.readTree(path.toFile());
                    while (tree.path("result").isObject()) tree = tree.path("result");
                    assertThat(tree.path("prChangeSummary").isObject()).as(path.toString()).isTrue();
                    inputs.put(path.toString(), mapper.convertValue(tree.path("prChangeSummary"), new TypeReference<>() {}));
                }
            }
            assertThat(inputs).as("The complete Martian50 response set must be persisted").hasSize(50);
        } else {
            // The normal build exercises a substantial payload; benchmark validation
            // uses the exact unmodified model results through the same service.
            inputs.put("contract-fixture", mapper.readValue("""
                    {"projectId":106935,"pullRequestId":9,"sourceRevision":"abc123",
                     "summary":"Retained behavior across all batches","files":[{"path":"a.py"}],
                     "graph":{"nodes":[{"id":"unit:a","path":"a.py"}],
                     "edges":[{"source":"unit:a","target":"unit:b","change":"added"}]}}
                    """, new TypeReference<>() {}));
            inputs.get("contract-fixture").put("retainedEvidence", "source-backed graph fact\n".repeat(50_000));
        }
        var transaction = new TransactionTemplate(transactions);
        var service = new PrChangeSummaryService(repository, mapper);
        var receipts = new ArrayList<Map<String, Object>>();
        for (var input : inputs.entrySet()) {
            var payload = input.getValue();
            long projectId = ((Number) payload.get("projectId")).longValue();
            long pr = ((Number) payload.get("pullRequestId")).longValue();
            String revision = (String) payload.get("sourceRevision");
            Long analysisId = transaction.execute(status -> {
                Project project = em.find(Project.class, projectId);
                if (project == null) {
                    String identity = UUID.randomUUID().toString();
                    var workspace = new Workspace(identity, identity, "benchmark persistence evidence");
                    em.persist(workspace); em.flush();
                    // Keep the benchmark identity exactly; never rewrite the input
                    // to make the service's tenant/revision checks pass.
                    em.createNativeQuery("""
                            INSERT INTO project (id,workspace_id,namespace,is_active,created_at,updated_at,
                                                 pr_analysis_enabled,branch_analysis_enabled)
                            VALUES (:id,:workspace,:namespace,true,now(),now(),true,true)
                            """).setParameter("id", projectId).setParameter("workspace", workspace.getId())
                            .setParameter("namespace", "benchmark-" + projectId).executeUpdate();
                    project = em.find(Project.class, projectId);
                }
                var analysis = new CodeAnalysis();
                analysis.setProject(project); analysis.setAnalysisType(AnalysisType.PR_REVIEW);
                analysis.setPrNumber(pr); analysis.setCommitHash(revision);
                service.attach(analysis, payload);
                em.persist(analysis); em.flush();
                return analysis.getId();
            });
            // Reload from a separate committed transaction, with the exact owning analysis.
            var restored = service.find(projectId, pr, revision, analysisId);
            assertThat(restored).as(input.getKey()).isEqualTo(payload);
            assertThat(service.find(projectId + 1_000_000, pr, revision, analysisId)).isNull();
            assertThat(service.find(projectId, pr + 1_000_000, revision, analysisId)).isNull();
            assertThat(service.find(projectId, pr, revision + "-different", analysisId)).isNull();
            String stored = repository.findById(analysisId).orElseThrow().getPrChangeSummaryPayload();
            assertThat(mapper.readTree(stored)).isEqualTo(mapper.valueToTree(payload));
            var receipt = new LinkedHashMap<String, Object>();
            receipt.put("input", input.getKey()); receipt.put("projectId", projectId);
            receipt.put("pr", pr); receipt.put("revision", revision); receipt.put("analysisId", analysisId);
            receipt.put("storedBytes", stored.getBytes(java.nio.charset.StandardCharsets.UTF_8).length);
            receipt.put("committedRoundtripEqual", true); receipt.put("wrongTenantPrRevisionRejected", true);
            receipts.add(receipt);
            String output = System.getenv("CODECROW_BENCHMARK_PERSISTENCE_EVIDENCE");
            if (output != null && !output.isBlank()) {
                Path root = Path.of(output); Files.createDirectories(root);
                mapper.writerWithDefaultPrettyPrinter().writeValue(root.resolve("roundtrip-receipts.json").toFile(), receipts);
                mapper.writerWithDefaultPrettyPrinter().writeValue(root.resolve(projectId + "-" + pr + "-reloaded.json").toFile(), restored);
            }
        }
        assertThat(receipts).hasSize(inputs.size());
    }
}
