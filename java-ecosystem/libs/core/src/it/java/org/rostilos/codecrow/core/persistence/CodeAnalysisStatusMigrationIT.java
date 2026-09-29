package org.rostilos.codecrow.core.persistence;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisStatus;
import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.datasource.init.ScriptUtils;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.SQLException;
import java.util.ArrayList;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/** Exercises the upgrade path; a fresh Hibernate schema already allows PARTIAL. */
@Testcontainers
class CodeAnalysisStatusMigrationIT {
    @Container
    private static final PostgreSQLContainer<?> DATABASE =
            new PostgreSQLContainer<>("postgres:16-alpine");

    @BeforeEach
    void resetTable() throws SQLException {
        try (Connection connection = connect(); var statement = connection.createStatement()) {
            statement.execute("DROP TABLE IF EXISTS code_analysis");
            statement.execute("""
                    CREATE TABLE code_analysis (
                        id BIGSERIAL PRIMARY KEY,
                        status VARCHAR(20) NOT NULL,
                        comment TEXT,
                        total_issues INTEGER NOT NULL DEFAULT 0
                    )
                    """);
        }
    }

    @Test
    void upgradesExistingConstraintWithoutLosingCompletedOrPartialFindings() throws SQLException {
        try (Connection connection = connect(); var statement = connection.createStatement()) {
            statement.execute("""
                    ALTER TABLE code_analysis ADD CONSTRAINT code_analysis_status_check
                    CHECK (status IN ('ACCEPTED', 'REJECTED', 'PENDING', 'ERROR'))
                    """);
            statement.execute("INSERT INTO code_analysis(status, comment) VALUES ('ACCEPTED', 'Prior review')");
            assertThatThrownBy(() -> statement.execute("INSERT INTO code_analysis(status) VALUES ('PARTIAL')"))
                    .isInstanceOf(SQLException.class)
                    .extracting(error -> ((SQLException) error).getSQLState())
                    .isEqualTo("23514");

            migrate(connection);
            statement.execute("""
                    INSERT INTO code_analysis(status, comment, total_issues)
                    VALUES ('PARTIAL', 'Vendor source unavailable; supported findings retained', 1)
                    """);
            try (var rows = statement.executeQuery("SELECT status, comment, total_issues FROM code_analysis ORDER BY id")) {
                assertThat(rows.next()).isTrue();
                assertThat(rows.getString("status")).isEqualTo("ACCEPTED");
                assertThat(rows.getString("comment")).isEqualTo("Prior review");
                assertThat(rows.next()).isTrue();
                assertThat(rows.getString("status")).isEqualTo("PARTIAL");
                assertThat(rows.getString("comment")).contains("Vendor source unavailable");
                assertThat(rows.getInt("total_issues")).isEqualTo(1);
                assertThat(rows.next()).isFalse();
            }
        }
    }

    @Test
    void supportsEveryLifecycleStateWithoutAnExistingConstraintAndOnRepeat() throws SQLException {
        try (Connection connection = connect(); var statement = connection.createStatement()) {
            migrate(connection);
            for (AnalysisStatus status : AnalysisStatus.values()) {
                try (var insert = connection.prepareStatement("INSERT INTO code_analysis(status) VALUES (?)")) {
                    insert.setString(1, status.name());
                    insert.executeUpdate();
                }
            }
            migrate(connection);
            List<String> persisted = new ArrayList<>();
            try (var rows = statement.executeQuery("SELECT status FROM code_analysis ORDER BY id")) {
                while (rows.next()) persisted.add(rows.getString(1));
            }
            assertThat(persisted).containsExactly(
                    java.util.Arrays.stream(AnalysisStatus.values()).map(Enum::name).toArray(String[]::new));
        }
    }

    @Test
    void skipsFreshDatabaseBeforeHibernateCreatesTheTable() throws SQLException {
        try (Connection connection = connect(); var statement = connection.createStatement()) {
            statement.execute("DROP TABLE code_analysis");
            migrate(connection);
            try (var row = statement.executeQuery("SELECT to_regclass('code_analysis')")) {
                assertThat(row.next()).isTrue();
                assertThat(row.getString(1)).isNull();
            }
        }
    }

    @Test
    void preservesLifecycleIntegrityAfterUpgrade() throws SQLException {
        try (Connection connection = connect(); var statement = connection.createStatement()) {
            migrate(connection);
            assertThatThrownBy(() -> statement.execute("INSERT INTO code_analysis(status) VALUES ('UNKNOWN')"))
                    .isInstanceOf(SQLException.class)
                    .extracting(error -> ((SQLException) error).getSQLState())
                    .isEqualTo("23514");
            assertThatThrownBy(() -> statement.execute("INSERT INTO code_analysis(status) VALUES (NULL)"))
                    .isInstanceOf(SQLException.class)
                    .extracting(error -> ((SQLException) error).getSQLState())
                    .isEqualTo("23502");
        }
    }

    private static Connection connect() throws SQLException {
        return DriverManager.getConnection(DATABASE.getJdbcUrl(), DATABASE.getUsername(), DATABASE.getPassword());
    }

    private static void migrate(Connection connection) {
        ScriptUtils.executeSqlScript(connection,
                new ClassPathResource("db/migration/managed/R__code_analysis_status.sql"));
    }
}
