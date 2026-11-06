package org.rostilos.codecrow.mcp.generic;

import okhttp3.OkHttpClient;
import org.rostilos.codecrow.vcsclient.VcsRateLimitInterceptor;
import org.rostilos.codecrow.vcsclient.VcsRateLimits;
import org.slf4j.LoggerFactory;
import org.springframework.data.redis.connection.RedisStandaloneConfiguration;
import org.springframework.data.redis.connection.lettuce.LettuceClientConfiguration;
import org.springframework.data.redis.connection.lettuce.LettuceConnectionFactory;
import org.springframework.data.redis.core.StringRedisTemplate;

import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.HexFormat;
import java.security.MessageDigest;

/** MCP uses the host's queue Redis database for cooldown timestamps only. */
public final class McpRateLimitTransport {
    private McpRateLimitTransport() {}

    private static final class Shared {
        private static final VcsRateLimits LIMITS = createLimits();
    }

    public static OkHttpClient observe(OkHttpClient client, String apiHost, String accessToken) {
        String identity = System.getProperty("vcs.quota.identity");
        if (identity == null || identity.isBlank()) {
            try {
                identity = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256")
                        .digest((apiHost + ":" + System.getProperty("project.id", "unknown") + ":" + accessToken).getBytes(StandardCharsets.UTF_8)));
            } catch (java.security.NoSuchAlgorithmException impossible) {
                throw new IllegalStateException(impossible);
            }
        }
        return client.newBuilder().retryOnConnectionFailure(false).addNetworkInterceptor(
                new VcsRateLimitInterceptor(Shared.LIMITS, identity, apiHost, "mcp")).build();
    }

    private static VcsRateLimits createLimits() {
        VcsRateLimits limits = new VcsRateLimits();
        String redisUrl = System.getenv("REDIS_URL");
        if (redisUrl == null || redisUrl.isBlank()) return limits;
        try {
            URI uri = URI.create(redisUrl);
            RedisStandaloneConfiguration config = new RedisStandaloneConfiguration(
                    uri.getHost(), uri.getPort() < 0 ? 6379 : uri.getPort());
            config.setDatabase(uri.getPath() == null || uri.getPath().length() < 2 ? 1
                    : Integer.parseInt(uri.getPath().substring(1)));
            if (uri.getUserInfo() != null) {
                String[] credentials = uri.getUserInfo().split(":", 2);
                if (credentials.length == 2) {
                    if (!credentials[0].isBlank()) config.setUsername(credentials[0]);
                    config.setPassword(credentials[1]);
                } else config.setPassword(credentials[0]);
            }
            var client = LettuceClientConfiguration.builder().commandTimeout(Duration.ofSeconds(2))
                    .clientOptions(io.lettuce.core.ClientOptions.builder().socketOptions(
                            io.lettuce.core.SocketOptions.builder().connectTimeout(Duration.ofSeconds(2)).build()).build());
            if ("rediss".equals(uri.getScheme())) client.useSsl();
            LettuceConnectionFactory factory = new LettuceConnectionFactory(config, client.build());
            factory.afterPropertiesSet();
            limits.useRedis(new StringRedisTemplate(factory));
            Runtime.getRuntime().addShutdownHook(new Thread(factory::destroy));
        } catch (RuntimeException unavailable) {
            LoggerFactory.getLogger(McpRateLimitTransport.class)
                    .warn("Shared MCP provider cooldown store unavailable; using process-local cooldowns");
        }
        return limits;
    }
}
