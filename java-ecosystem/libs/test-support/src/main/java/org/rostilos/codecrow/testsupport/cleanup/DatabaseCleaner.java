package org.rostilos.codecrow.testsupport.cleanup;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

import javax.sql.DataSource;
import java.util.List;

/**
 * Database cleaner that truncates all application tables between tests.
 * Uses TRUNCATE ... CASCADE for maximum isolation.
 */
@Component
public class DatabaseCleaner {

    private final JdbcTemplate jdbc;

    public DatabaseCleaner(DataSource dataSource) {
        this.jdbc = new JdbcTemplate(dataSource);
    }

    /**
     * Truncate all non-flyway tables in the public schema.
     */
    public void cleanAll() {
        List<String> tables = jdbc.queryForList(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' " +
                        "AND tablename NOT LIKE 'flyway_%'",
                String.class
        );

        if (!tables.isEmpty()) {
            String tableList = String.join(", ", tables);
            jdbc.execute("TRUNCATE TABLE " + tableList + " CASCADE");
        }
    }
}
