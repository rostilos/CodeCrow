package org.rostilos.codecrow.vcsclient.github;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.SignatureAlgorithm;
import okhttp3.*;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.KeyFactory;
import java.security.PrivateKey;
import java.security.spec.PKCS8EncodedKeySpec;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.Base64;
import java.util.Date;

/**
 * Service for GitHub App authentication.
 * 
 * GitHub Apps use a two-step authentication process:
 * 1. Generate a JWT signed with the app's private key to authenticate as the app
 * 2. Exchange the JWT for an installation access token to act on behalf of an installation
 * 
 * Installation access tokens expire after 1 hour.
 */
public class GitHubAppAuthService {
    
    private static final Logger log = LoggerFactory.getLogger(GitHubAppAuthService.class);
    private static final String GITHUB_API_BASE = "https://api.github.com";
    private static final MediaType JSON_MEDIA_TYPE = MediaType.parse("application/json");
    private static final ObjectMapper objectMapper = new ObjectMapper();
    
    private final String appId;
    private final PrivateKey privateKey;
    private final OkHttpClient httpClient;
    private final String apiBaseUrl;
    
    public GitHubAppAuthService(String appId, String privateKeyPath) throws Exception {
        this.appId = appId;
        this.privateKey = loadPrivateKey(privateKeyPath);
        this.httpClient = new OkHttpClient();
        this.apiBaseUrl = GITHUB_API_BASE;
    }
    
    public GitHubAppAuthService(String appId, PrivateKey privateKey) {
        this.appId = appId;
        this.privateKey = privateKey;
        this.httpClient = new OkHttpClient();
        this.apiBaseUrl = GITHUB_API_BASE;
    }

    GitHubAppAuthService(
            String appId,
            PrivateKey privateKey,
            OkHttpClient httpClient,
            String apiBaseUrl) {
        this.appId = appId;
        this.privateKey = privateKey;
        this.httpClient = httpClient;
        this.apiBaseUrl = apiBaseUrl.endsWith("/")
                ? apiBaseUrl.substring(0, apiBaseUrl.length() - 1)
                : apiBaseUrl;
    }
    
    /**
     * Load a private key from a PEM file.
     * Supports both PKCS#1 (RSA PRIVATE KEY) and PKCS#8 (PRIVATE KEY) formats.
     * GitHub generates keys in PKCS#1 format.
     */
    private PrivateKey loadPrivateKey(String path) throws Exception {
        String keyContent = Files.readString(Path.of(path));
        return parsePrivateKeyContent(keyContent);
    }

    /**
     * Parse PEM-encoded private key content into a {@link PrivateKey}.
     * Supports both PKCS#1 (RSA PRIVATE KEY) and PKCS#8 (PRIVATE KEY) formats.
     * <p>
     * This is a public static utility so that callers (e.g. VcsClientProvider)
     * can build a {@code PrivateKey} from PEM content stored in the database
     * without needing to write the content to a temporary file.
     */
    public static PrivateKey parsePrivateKeyContent(String pemContent) throws Exception {
        boolean isPkcs1 = pemContent.contains("-----BEGIN RSA PRIVATE KEY-----");

        String stripped = pemContent
                .replace("-----BEGIN RSA PRIVATE KEY-----", "")
                .replace("-----END RSA PRIVATE KEY-----", "")
                .replace("-----BEGIN PRIVATE KEY-----", "")
                .replace("-----END PRIVATE KEY-----", "")
                .replaceAll("\\s", "");

        byte[] keyBytes = Base64.getDecoder().decode(stripped);
        KeyFactory keyFactory = KeyFactory.getInstance("RSA");

        if (isPkcs1) {
            return parsePkcs1PrivateKey(keyBytes, keyFactory);
        } else {
            PKCS8EncodedKeySpec spec = new PKCS8EncodedKeySpec(keyBytes);
            return keyFactory.generatePrivate(spec);
        }
    }
    
    /**
     * Parse PKCS#1 RSA private key using manual ASN.1 DER parsing.
     * PKCS#1 RSAPrivateKey structure:
     * RSAPrivateKey ::= SEQUENCE {
     *   version           Version,
     *   modulus           INTEGER,  -- n
     *   publicExponent    INTEGER,  -- e
     *   privateExponent   INTEGER,  -- d
     *   prime1            INTEGER,  -- p
     *   prime2            INTEGER,  -- q
     *   exponent1         INTEGER,  -- d mod (p-1)
     *   exponent2         INTEGER,  -- d mod (q-1)
     *   coefficient       INTEGER   -- (inverse of q) mod p
     * }
     */
    private static PrivateKey parsePkcs1PrivateKey(byte[] pkcs1Bytes, KeyFactory keyFactory) throws Exception {
        Asn1DerParser parser = new Asn1DerParser(pkcs1Bytes);
        parser.readSequence();
        
        parser.readInteger();
        java.math.BigInteger modulus = parser.readInteger();
        java.math.BigInteger publicExponent = parser.readInteger();
        java.math.BigInteger privateExponent = parser.readInteger();
        java.math.BigInteger primeP = parser.readInteger();
        java.math.BigInteger primeQ = parser.readInteger();
        java.math.BigInteger primeExponentP = parser.readInteger();
        java.math.BigInteger primeExponentQ = parser.readInteger();
        java.math.BigInteger crtCoefficient = parser.readInteger();
        
        java.security.spec.RSAPrivateCrtKeySpec keySpec = new java.security.spec.RSAPrivateCrtKeySpec(
                modulus, publicExponent, privateExponent, primeP, primeQ,
                primeExponentP, primeExponentQ, crtCoefficient
        );
        
        return keyFactory.generatePrivate(keySpec);
    }

    private static class Asn1DerParser {
        private final byte[] data;
        private int pos = 0;
        
        Asn1DerParser(byte[] data) {
            this.data = data;
        }
        
        void readSequence() {
            if (data[pos++] != 0x30) {
                throw new IllegalArgumentException("Expected SEQUENCE tag");
            }
            readLength();
        }
        
        java.math.BigInteger readInteger() {
            if (data[pos++] != 0x02) {
                throw new IllegalArgumentException("Expected INTEGER tag");
            }
            int len = readLength();
            byte[] value = new byte[len];
            System.arraycopy(data, pos, value, 0, len);
            pos += len;
            return new java.math.BigInteger(1, value);
        }
        
        private int readLength() {
            int len = data[pos++] & 0xFF;
            if (len < 128) {
                return len;
            }
            int numBytes = len & 0x7F;
            len = 0;
            for (int i = 0; i < numBytes; i++) {
                len = (len << 8) | (data[pos++] & 0xFF);
            }
            return len;
        }
    }

    public String generateAppJwt() {
        Instant now = Instant.now();
        
        return Jwts.builder()
                .setIssuer(appId)
                .setIssuedAt(Date.from(now.minusSeconds(60)))
                .setExpiration(Date.from(now.plusSeconds(600)))
                .signWith(privateKey, SignatureAlgorithm.RS256)
                .compact();
    }
    
    /**
     * Get an installation access token for a specific installation.
     * This token is used to make API calls on behalf of the installation.
     * 
     * @param installationId the GitHub App installation ID
     * @return installation access token response
     */
    public InstallationToken getInstallationAccessToken(long installationId) throws IOException {
        String jwt = generateAppJwt();
        
        Request request = new Request.Builder()
                .url(apiBaseUrl + "/app/installations/" + installationId + "/access_tokens")
                .header("Authorization", "Bearer " + jwt)
                .header("Accept", "application/vnd.github+json")
                .header("X-GitHub-Api-Version", "2022-11-28")
                .post(RequestBody.create("", JSON_MEDIA_TYPE))
                .build();
        
        try (Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                String errorBody = response.body() != null ? response.body().string() : "";
                log.error("Failed to get installation access token: {} - {}", response.code(), errorBody);
                throw new IOException("Failed to get installation access token: " + response.code());
            }
            
            JsonNode json = objectMapper.readTree(response.body().string());
            String token = json.get("token").asText();
            String expiresAt = json.get("expires_at").asText();
            
            Instant expiry = Instant.parse(expiresAt);
            LocalDateTime expiresAtLocal = LocalDateTime.ofInstant(expiry, ZoneOffset.UTC);
            
            log.debug("Got installation access token, expires at: {}", expiresAtLocal);
            
            return new InstallationToken(token, expiresAtLocal);
        }
    }
    
    /**
     * Get information about a specific installation.
     */
    public InstallationInfo getInstallation(long installationId) throws IOException {
        String jwt = generateAppJwt();
        
        Request request = new Request.Builder()
                .url(apiBaseUrl + "/app/installations/" + installationId)
                .header("Authorization", "Bearer " + jwt)
                .header("Accept", "application/vnd.github+json")
                .header("X-GitHub-Api-Version", "2022-11-28")
                .get()
                .build();
        
        try (Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                String errorBody = response.body() != null ? response.body().string() : "";
                log.error("Failed to get installation info: {} - {}", response.code(), errorBody);
                throw new IOException("Failed to get installation info: " + response.code());
            }
            
            JsonNode json = objectMapper.readTree(response.body().string());
            return parseInstallationInfo(json);
        }
    }
    
    /**
     * List all installations for this GitHub App.
     *
     * <p><strong>Security:</strong> this is App-global administrative inventory.
     * Its contents must never be used to select or authorize an installation
     * for a tenant. Use {@link #canUserAccessInstallation(String, InstallationInfo)}
     * with a GitHub user access token for that decision.</p>
     */
    public java.util.List<InstallationInfo> listInstallations() throws IOException {
        String jwt = generateAppJwt();
        java.util.List<InstallationInfo> installations = new java.util.ArrayList<>();
        
        Request request = new Request.Builder()
                .url(apiBaseUrl + "/app/installations")
                .header("Authorization", "Bearer " + jwt)
                .header("Accept", "application/vnd.github+json")
                .header("X-GitHub-Api-Version", "2022-11-28")
                .get()
                .build();
        
        try (Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                String errorBody = response.body() != null ? response.body().string() : "";
                log.error("Failed to list installations: {} - {}", response.code(), errorBody);
                throw new IOException("Failed to list installations: " + response.code());
            }
            
            JsonNode json = objectMapper.readTree(response.body().string());
            if (json.isArray()) {
                for (JsonNode node : json) {
                    installations.add(parseInstallationInfo(node));
                }
            }
        }
        
        return installations;
    }

    /**
     * Verify that the exact installation is visible to the authenticated
     * GitHub user. GitHub explicitly recommends this user-scoped check before
     * trusting an installation ID returned by the setup callback.
     *
     * <p>This check is deliberately separate from app JWT authentication. An
     * app JWT can see and mint tokens for every installation of the App, so it
     * cannot prove that a CodeCrow user is allowed to attach an installation to
     * a workspace.</p>
     *
     * <p>This intentionally does not require organization-owner permissions.
     * An ordinary organization member can request an installation and later
     * use it once the owner approves the request.</p>
     */
    public boolean canUserAccessInstallation(
            String userAccessToken,
            InstallationInfo installation) throws IOException {
        if (userAccessToken == null || userAccessToken.isBlank()) {
            return false;
        }

        UserInfo user = getAuthenticatedUser(userAccessToken);
        boolean installationAccessible = listInstallationsForUser(userAccessToken).stream()
                .anyMatch(candidate -> candidate.installationId() == installation.installationId());
        if (!installationAccessible) {
            log.warn("GitHub user {} cannot access installation {}", user.login(), installation.installationId());
            return false;
        }

        return !("User".equalsIgnoreCase(installation.accountType())
                || "User".equalsIgnoreCase(installation.targetType()))
                || user.id() == installation.accountId();
    }

    /**
     * List pending installation requests visible to the App JWT. These records
     * contain GitHub's immutable request ID, requester, and target account and
     * are suitable for binding a CodeCrow request intent before approval.
     */
    public java.util.List<InstallationRequestInfo> listInstallationRequests() throws IOException {
        String jwt = generateAppJwt();
        java.util.List<InstallationRequestInfo> requests = new java.util.ArrayList<>();
        int page = 1;

        while (true) {
            Request request = new Request.Builder()
                    .url(apiBaseUrl + "/app/installation-requests?per_page=100&page=" + page)
                    .header("Authorization", "Bearer " + jwt)
                    .header("Accept", "application/vnd.github+json")
                    .header("X-GitHub-Api-Version", "2022-11-28")
                    .get()
                    .build();

            int pageSize = 0;
            try (Response response = httpClient.newCall(request).execute()) {
                if (!response.isSuccessful()) {
                    String errorBody = response.body() != null ? response.body().string() : "";
                    log.warn("Failed to list GitHub App installation requests: {} - {}",
                            response.code(), errorBody);
                    throw new IOException("Failed to list GitHub App installation requests: " + response.code());
                }

                JsonNode json = objectMapper.readTree(response.body().string());
                if (json.isArray()) {
                    for (JsonNode node : json) {
                        JsonNode account = node.path("account");
                        JsonNode requester = node.path("requester");
                        requests.add(new InstallationRequestInfo(
                                node.path("id").asLong(),
                                account.path("id").asLong(),
                                account.path("login").asText(),
                                account.path("type").asText(),
                                requester.path("id").asLong(),
                                requester.path("login").asText(),
                                Instant.parse(node.path("created_at").asText())
                        ));
                        pageSize++;
                    }
                }
            }

            if (pageSize < 100) {
                break;
            }
            page++;
        }

        return requests;
    }

    /**
     * List installations visible to a GitHub App user-access token. Unlike
     * {@link #listInstallations()}, this endpoint is scoped to the authenticated
     * GitHub user and is suitable for authorization checks.
     */
    public java.util.List<InstallationInfo> listInstallationsForUser(String userAccessToken)
            throws IOException {
        java.util.List<InstallationInfo> installations = new java.util.ArrayList<>();
        int page = 1;

        while (true) {
            Request request = new Request.Builder()
                    .url(apiBaseUrl + "/user/installations?per_page=100&page=" + page)
                    .header("Authorization", "Bearer " + userAccessToken)
                    .header("Accept", "application/vnd.github+json")
                    .header("X-GitHub-Api-Version", "2022-11-28")
                    .get()
                    .build();

            int pageSize = 0;
            try (Response response = httpClient.newCall(request).execute()) {
                if (!response.isSuccessful()) {
                    String errorBody = response.body() != null ? response.body().string() : "";
                    log.warn("Failed to list installations for GitHub user: {} - {}",
                            response.code(), errorBody);
                    throw new IOException("Failed to verify GitHub user installation access: " + response.code());
                }

                JsonNode json = objectMapper.readTree(response.body().string());
                JsonNode pageInstallations = json.path("installations");
                if (pageInstallations.isArray()) {
                    for (JsonNode node : pageInstallations) {
                        installations.add(parseInstallationInfo(node));
                        pageSize++;
                    }
                }
            }

            if (pageSize < 100) {
                break;
            }
            page++;
        }

        return installations;
    }

    public UserInfo getAuthenticatedUser(String userAccessToken) throws IOException {
        Request request = new Request.Builder()
                .url(apiBaseUrl + "/user")
                .header("Authorization", "Bearer " + userAccessToken)
                .header("Accept", "application/vnd.github+json")
                .header("X-GitHub-Api-Version", "2022-11-28")
                .get()
                .build();

        try (Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                throw new IOException("Failed to verify GitHub user identity: " + response.code());
            }
            JsonNode json = objectMapper.readTree(response.body().string());
            return new UserInfo(json.path("id").asLong(), json.path("login").asText());
        }
    }
    
    private InstallationInfo parseInstallationInfo(JsonNode json) {
        long id = json.get("id").asLong();
        JsonNode account = json.get("account");
        String accountLogin = account.get("login").asText();
        String accountType = account.get("type").asText();
        String accountAvatarUrl = account.has("avatar_url") ? account.get("avatar_url").asText() : null;
        long accountId = account.get("id").asLong();
        
        String targetType = json.has("target_type") ? json.get("target_type").asText() : accountType;
        
        return new InstallationInfo(
                id,
                accountId,
                accountLogin,
                accountType,
                accountAvatarUrl,
                targetType
        );
    }

    public record InstallationToken(String token, LocalDateTime expiresAt) {}

    public record UserInfo(long id, String login) {}

    public record InstallationRequestInfo(
            long requestId,
            long accountId,
            String accountLogin,
            String accountType,
            long requesterId,
            String requesterLogin,
            Instant createdAt
    ) {}

    public record InstallationInfo(
            long installationId,
            long accountId,
            String accountLogin,
            String accountType,  // "User" or "Organization"
            String accountAvatarUrl,
            String targetType    // "User" or "Organization"
    ) {}
}
