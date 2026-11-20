package org.rostilos.codecrow.core.service;

import org.junit.jupiter.api.Test;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.nio.charset.StandardCharsets;
import java.sql.DriverManager;
import java.util.ArrayList;

import static org.assertj.core.api.Assertions.assertThat;

@Testcontainers(disabledWithoutDocker = true)
class ReviewCheckMigrationTest {
    @Container
    static final PostgreSQLContainer<?> database = new PostgreSQLContainer<>("postgres:16-alpine");

    @Test
    void movesLegacyAdvisoriesWithoutLosingHistoryOrCrossingTenantAndOrdinaryIssueBoundaries() throws Exception {
        try (var connection = DriverManager.getConnection(database.getJdbcUrl(), database.getUsername(), database.getPassword());
             var sql = connection.createStatement()) {
            sql.execute("""
                CREATE TABLE project(id bigint PRIMARY KEY);
                INSERT INTO project VALUES (402), (999);
                CREATE TABLE code_analysis (id bigint PRIMARY KEY, project_id bigint, pr_number bigint,
                    total_issues integer DEFAULT 0, high_severity_count integer DEFAULT 0, medium_severity_count integer DEFAULT 0,
                    low_severity_count integer DEFAULT 0, info_severity_count integer DEFAULT 0, resolved_count integer DEFAULT 0);
                CREATE TABLE code_analysis_issue (id bigint PRIMARY KEY, analysis_id bigint, title text, reason text,
                    file_path text, issue_category text DEFAULT 'SECURITY', tracked_from_issue_id bigint,
                    severity varchar(10) DEFAULT 'HIGH', line_number integer DEFAULT 1, code_snippet text, suggested_fix_description text,
                    is_resolved boolean DEFAULT false, manual_status_resolved boolean, manual_status_updated_at timestamptz,
                    resolved_by text, resolved_description text);
                CREATE TABLE branch (id bigint PRIMARY KEY, project_id bigint,
                    total_issues integer DEFAULT 0, high_severity_count integer DEFAULT 0, medium_severity_count integer DEFAULT 0,
                    low_severity_count integer DEFAULT 0, info_severity_count integer DEFAULT 0, resolved_count integer DEFAULT 0);
                CREATE TABLE branch_issue (id bigint PRIMARY KEY, branch_id bigint, origin_issue_id bigint, origin_analysis_id bigint,
                    is_resolved boolean DEFAULT false, severity varchar(10) DEFAULT 'HIGH');
                INSERT INTO code_analysis(id, project_id, pr_number) VALUES (1, 402, 42), (2, 402, 42), (3, 999, 42), (4, 402, 99);
                INSERT INTO code_analysis_issue(id, analysis_id, title, reason, file_path, tracked_from_issue_id) VALUES
                  (1, 1, 'vendor/package: TEST-001', 'Composer audit: vendor/package v1.0 is affected by TEST-001. Fixture', 'composer.lock', NULL),
                  (2, 2, 'Reworded title', 'Issue still remains', 'composer.json', 1),
                  (3, 2, 'Another rewritten title', 'Still present', 'composer.json', 2),
                  (4, 2, 'vendor/package: TEST-001', 'Issue still remains', 'composer.json', NULL),
                  (5, 2, 'Unsafe script execution', 'Ordinary security issue', 'composer.json', NULL),
                  (6, 3, 'Foreign project', 'Still remains', 'composer.json', 1),
                  (7, 4, 'Foreign PR', 'Still remains', 'composer.json', 1),
                  (8, 2, 'vendor/package: TEST-001', 'Ordinary issue in another root', 'app/composer.json', NULL),
                  (9, 2, 'Different title', 'Composer audit: vendor/package v1.0 is affected by TEST-001. Fixture', 'composer.json', NULL),
                  (10, 3, 'vendor/package: TEST-001', 'Unrelated same title in another project', 'composer.json', NULL),
                  (11, 4, 'vendor/package: TEST-001', 'Unrelated same title in another PR', 'composer.json', NULL),
                  (12, 2, 'Reworded title', 'Unrelated issue sharing a generic title', 'composer.json', NULL),
                  (13, 2, 'vendor/package: TEST-002', 'Composer audit: vendor/package v1.0 is affected by TEST-002. Other advisory', 'composer.json', 1);
                UPDATE code_analysis_issue SET is_resolved = true, manual_status_resolved = true,
                    manual_status_updated_at = '2026-11-06T10:00:00Z' WHERE id = 2;
                INSERT INTO branch(id, project_id) VALUES (1, 402), (2, 999);
                INSERT INTO branch_issue(id, branch_id, origin_issue_id) VALUES (1, 1, 1), (2, 2, 1), (3, 1, 5);
                """);
            String migration;
            try (var source = getClass().getResourceAsStream("/db/migration/managed/R__review_check_source.sql")) {
                migration = new String(source.readAllBytes(), StandardCharsets.UTF_8);
            }
            sql.execute(migration);
            sql.execute(migration);
            var remaining = new ArrayList<Long>();
            try (var rows = sql.executeQuery("SELECT id FROM code_analysis_issue ORDER BY id")) {
                while (rows.next()) remaining.add(rows.getLong(1));
            }
            assertThat(remaining).containsExactly(5L, 6L, 7L, 8L, 9L, 10L, 11L, 12L);
            try (var rows = sql.executeQuery("SELECT count(*) FROM review_check_finding")) {
                rows.next(); assertThat(rows.getLong(1)).isEqualTo(3L);
            }
            try (var rows = sql.executeQuery("SELECT count(*) FROM review_check_legacy_record")) {
                rows.next(); assertThat(rows.getLong(1)).isEqualTo(6L);
            }
            try (var rows = sql.executeQuery("SELECT finding_key, is_resolved, manual_status_resolved FROM review_check_finding f JOIN review_check_result r ON r.id=f.result_id WHERE r.analysis_id=2 AND f.title='vendor/package: TEST-001'")) {
                assertThat(rows.next()).isTrue();
                assertThat(rows.getString(1)).isEqualTo(org.rostilos.codecrow.core.model.reviewcheck.ReviewCheckFinding.key("\0vendor/package: TEST-001"));
                assertThat(rows.getBoolean(2)).isTrue(); assertThat(rows.getBoolean(3)).isTrue();
            }
            try (var rows = sql.executeQuery("SELECT payload->>'reason' FROM review_check_legacy_record WHERE source_table='code_analysis_issue' AND source_id=3")) {
                assertThat(rows.next()).isTrue(); assertThat(rows.getString(1)).isEqualTo("Still present");
            }
            try (var rows = sql.executeQuery("SELECT id FROM branch_issue ORDER BY id")) {
                assertThat(rows.next()).isTrue(); assertThat(rows.getLong(1)).isEqualTo(2L);
                assertThat(rows.next()).isTrue(); assertThat(rows.getLong(1)).isEqualTo(3L); assertThat(rows.next()).isFalse();
            }
            try (var rows = sql.executeQuery("SELECT total_issues FROM code_analysis WHERE id=2")) {
                rows.next(); assertThat(rows.getInt(1)).isEqualTo(4);
            }
            try (var rows = sql.executeQuery("SELECT count(*) FROM information_schema.columns WHERE table_name IN ('code_analysis_issue','branch_issue') AND column_name='review_check_id'")) {
                rows.next(); assertThat(rows.getInt(1)).isZero();
            }
        }
    }
}
