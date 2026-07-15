package org.rostilos.codecrow.testsupport.legacy;

import java.util.List;
import java.util.Objects;

/** Immutable endpoint facades for services created outside the guarded JVM. */
public final class LegacyContainerEndpoints {

    static final String LOOPBACK = "127.0.0.1";
    static final int POSTGRES_PORT = 15432;
    static final int REDIS_PORT = 16379;
    static final String POSTGRES_DATABASE = "p007_acceptance";
    static final String POSTGRES_USERNAME = "offline_fixture";
    static final String POSTGRES_PASSWORD = "offline_fixture_only";

    private LegacyContainerEndpoints() {
    }

    public static final class PostgresEndpoint {

        private final String host;
        private final int port;
        private final String database;
        private final String username;
        private final String password;

        public PostgresEndpoint(
                String host,
                int port,
                String database,
                String username,
                String password
        ) {
            requireFixedEndpoint(host, port, POSTGRES_PORT, "PostgreSQL");
            this.host = host;
            this.port = port;
            this.database = requireReviewed(database, POSTGRES_DATABASE, "database");
            this.username = requireReviewed(username, POSTGRES_USERNAME, "username");
            this.password = requireReviewed(password, POSTGRES_PASSWORD, "password");
        }

        public String host() {
            return host;
        }

        public int port() {
            return port;
        }

        public String database() {
            return database;
        }

        public String username() {
            return username;
        }

        public String jdbcUrl() {
            return "jdbc:postgresql://" + host + ":" + port + "/" + database;
        }

        public List<String> springProperties() {
            return List.of(
                    "spring.datasource.url=" + jdbcUrl(),
                    "spring.datasource.username=" + username,
                    "spring.datasource.password=" + password,
                    "spring.datasource.driver-class-name=org.postgresql.Driver"
            );
        }

        @Override
        public String toString() {
            return "PostgresEndpoint[host=" + host
                    + ", port=" + port
                    + ", database=" + database
                    + ", username=" + username
                    + ", password=<redacted>]";
        }
    }

    public record RedisEndpoint(String host, int port) {

        public RedisEndpoint {
            requireFixedEndpoint(host, port, REDIS_PORT, "Redis");
        }

        public String getHost() {
            return host;
        }

        public Integer getMappedPort(int containerPort) {
            if (containerPort != 6379) {
                throw new IllegalArgumentException(
                        "guarded Redis exposes only the reviewed container port 6379"
                );
            }
            return port;
        }

        public List<String> springProperties() {
            return List.of(
                    "spring.redis.host=" + host,
                    "spring.redis.port=" + port
            );
        }
    }

    private static void requireFixedEndpoint(
            String host,
            int actualPort,
            int requiredPort,
            String service
    ) {
        if (!LOOPBACK.equals(Objects.requireNonNull(host, "host"))) {
            throw new IllegalStateException(
                    service + " host must be the exact literal 127.0.0.1"
            );
        }
        if (actualPort != requiredPort) {
            throw new IllegalStateException(
                    service + " port must be the fixed guarded port " + requiredPort
            );
        }
    }

    private static String requireReviewed(String value, String expected, String field) {
        if (!expected.equals(value)) {
            throw new IllegalStateException(
                    "PostgreSQL " + field + " does not match the reviewed fixture"
            );
        }
        return value;
    }
}
