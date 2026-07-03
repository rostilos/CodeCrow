package org.rostilos.codecrow.testsupport.cleanup;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.ConnectionCallback;
import org.springframework.stereotype.Component;
import org.springframework.dao.DataAccessException;

import javax.sql.DataSource;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.List;

/**
 * Database cleaner that truncates all application tables between tests.
 * Uses TRUNCATE ... CASCADE for maximum isolation.
 */
@Component
public class DatabaseCleaner {

    private static final long CLEANUP_LOCK_KEY = 0x434F444543524F57L; // CODECROW
    private static final int MAX_CLEANUP_ATTEMPTS = 3;

    private final JdbcTemplate jdbc;

    public DatabaseCleaner(DataSource dataSource) {
        this.jdbc = new JdbcTemplate(dataSource);
    }

    /**
     * Truncate all non-flyway tables in the public schema.
     */
    public void cleanAll() {
        for (int attempt = 1; attempt <= MAX_CLEANUP_ATTEMPTS; attempt++) {
            try {
                cleanAllOnce();
                return;
            } catch (DataAccessException e) {
                if (attempt == MAX_CLEANUP_ATTEMPTS || !isDeadlock(e)) {
                    throw e;
                }
                sleepBeforeRetry(attempt);
            }
        }
    }

    private void cleanAllOnce() {
        jdbc.execute((ConnectionCallback<Void>) connection -> {
            boolean originalAutoCommit = connection.getAutoCommit();
            connection.setAutoCommit(false);
            try {
                try (Statement statement = connection.createStatement()) {
                    statement.execute("SELECT pg_advisory_xact_lock(" + CLEANUP_LOCK_KEY + ")");
                }

                List<String> tables = listApplicationTables(connection.createStatement());
                if (!tables.isEmpty()) {
                    try (Statement statement = connection.createStatement()) {
                        statement.execute("TRUNCATE TABLE " + String.join(", ", tables) + " CASCADE");
                    }
                }

                connection.commit();
                return null;
            } catch (SQLException | RuntimeException e) {
                rollbackQuietly(connection, e);
                throw e;
            } finally {
                connection.setAutoCommit(originalAutoCommit);
            }
        });
    }

    private List<String> listApplicationTables(Statement statement) throws SQLException {
        List<String> tables = new ArrayList<>();
        try (statement;
             ResultSet resultSet = statement.executeQuery(
                     "SELECT quote_ident(tablename) " +
                             "FROM pg_tables " +
                             "WHERE schemaname = 'public' " +
                             "AND tablename NOT LIKE 'flyway_%' " +
                             "ORDER BY tablename")) {
            while (resultSet.next()) {
                tables.add(resultSet.getString(1));
            }
        }
        return tables;
    }

    private boolean isDeadlock(Throwable throwable) {
        Throwable current = throwable;
        while (current != null) {
            if (current instanceof SQLException sqlException && "40P01".equals(sqlException.getSQLState())) {
                return true;
            }
            String message = current.getMessage();
            if (message != null && message.toLowerCase().contains("deadlock detected")) {
                return true;
            }
            current = current.getCause();
        }
        return false;
    }

    private void sleepBeforeRetry(int attempt) {
        try {
            Thread.sleep(250L * attempt);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("Interrupted while retrying database cleanup", e);
        }
    }

    private void rollbackQuietly(java.sql.Connection connection, Exception originalException) {
        try {
            connection.rollback();
        } catch (SQLException rollbackException) {
            originalException.addSuppressed(rollbackException);
        }
    }
}
