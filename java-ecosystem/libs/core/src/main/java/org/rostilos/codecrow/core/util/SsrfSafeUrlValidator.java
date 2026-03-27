package org.rostilos.codecrow.core.util;

import java.net.InetAddress;
import java.net.MalformedURLException;
import java.net.URL;
import java.net.UnknownHostException;

/**
 * Validates user-provided URLs to prevent Server-Side Request Forgery (SSRF).
 * <p>
 * Performs DNS resolution and rejects URLs whose hostnames resolve to
 * private, loopback, link-local, or other non-public IP addresses.
 * <p>
 * The env variable {@code ALLOW_PRIVATE_ENDPOINTS=true} disables the
 * private-IP check — intended for self-hosted deployments that legitimately
 * use internal URLs for LLM endpoints.
 */
public final class SsrfSafeUrlValidator {

    private SsrfSafeUrlValidator() {
        // Utility class — no instantiation
    }

    /**
     * Whether private/reserved IPs are allowed (for self-hosted deployments).
     * Controlled by the {@code ALLOW_PRIVATE_ENDPOINTS} environment variable.
     */
    private static final boolean ALLOW_PRIVATE_ENDPOINTS =
            "true".equalsIgnoreCase(System.getenv("ALLOW_PRIVATE_ENDPOINTS"));

    /**
     * CGNAT range 100.64.0.0/10 — not covered by JDK's built-in checks.
     */
    private static final byte CGNAT_FIRST_OCTET = 100;
    private static final int CGNAT_SECOND_OCTET_MIN = 64;
    private static final int CGNAT_SECOND_OCTET_MAX = 127;

    /**
     * Validate a user-provided URL for SSRF safety.
     *
     * @param url the URL string to validate
     * @throws IllegalArgumentException if the URL is malformed, uses a non-HTTPS scheme,
     *                                  or resolves to a private/reserved IP address
     */
    public static void validate(String url) {
        if (url == null || url.isBlank()) {
            throw new IllegalArgumentException("URL must not be empty");
        }

        // 1. Parse and validate scheme
        URL parsed;
        try {
            parsed = new URL(url);
        } catch (MalformedURLException e) {
            throw new IllegalArgumentException("Invalid URL format: " + e.getMessage());
        }

        String scheme = parsed.getProtocol().toLowerCase();
        if (!"https".equals(scheme)) {
            throw new IllegalArgumentException(
                    "Only HTTPS URLs are allowed for custom endpoints. Got: " + scheme);
        }

        String host = parsed.getHost();
        if (host == null || host.isBlank()) {
            throw new IllegalArgumentException("URL must have a valid hostname");
        }

        // 2. Reject obviously dangerous hostnames
        String hostLower = host.toLowerCase();
        if ("localhost".equals(hostLower) || hostLower.endsWith(".local")) {
            throw new IllegalArgumentException(
                    "localhost and .local hostnames are not allowed for custom endpoints");
        }

        // 3. Skip IP checks if self-hosted mode allows private endpoints
        if (ALLOW_PRIVATE_ENDPOINTS) {
            return;
        }

        // 4. DNS resolve and validate all resolved IPs
        InetAddress[] addresses;
        try {
            addresses = InetAddress.getAllByName(host);
        } catch (UnknownHostException e) {
            throw new IllegalArgumentException(
                    "Cannot resolve hostname: " + host);
        }

        for (InetAddress addr : addresses) {
            if (isPrivateOrReserved(addr)) {
                throw new IllegalArgumentException(
                        "Custom endpoint URL resolves to a private/reserved IP address (" +
                                addr.getHostAddress() + "). " +
                                "For self-hosted deployments, set ALLOW_PRIVATE_ENDPOINTS=true.");
            }
        }
    }

    /**
     * Check if an IP address is private, reserved, or otherwise non-public.
     * Covers all ranges that JDK's InetAddress methods handle, plus additional
     * ranges that JDK misses (CGNAT, documentation nets, benchmarking, reserved).
     */
    static boolean isPrivateOrReserved(InetAddress addr) {
        // JDK built-in checks
        if (addr.isLoopbackAddress()) return true;       // 127.0.0.0/8, ::1
        if (addr.isSiteLocalAddress()) return true;      // 10/8, 172.16/12, 192.168/16, fc00::/7
        if (addr.isLinkLocalAddress()) return true;      // 169.254/16, fe80::/10
        if (addr.isMulticastAddress()) return true;      // 224/4, ff00::/8
        if (addr.isAnyLocalAddress()) return true;       // 0.0.0.0, ::

        byte[] bytes = addr.getAddress();

        // IPv4-specific additional checks
        if (bytes.length == 4) {
            int b0 = bytes[0] & 0xFF;
            int b1 = bytes[1] & 0xFF;

            // 0.0.0.0/8 — "This host on this network"
            if (b0 == 0) return true;

            // 100.64.0.0/10 — CGNAT (Carrier-Grade NAT)
            if (b0 == CGNAT_FIRST_OCTET && b1 >= CGNAT_SECOND_OCTET_MIN && b1 <= CGNAT_SECOND_OCTET_MAX) return true;

            // 192.0.0.0/24 — IETF Protocol Assignments
            if (b0 == 192 && b1 == 0 && (bytes[2] & 0xFF) == 0) return true;

            // 192.0.2.0/24 — Documentation (TEST-NET-1)
            if (b0 == 192 && b1 == 0 && (bytes[2] & 0xFF) == 2) return true;

            // 192.88.99.0/24 — 6to4 Relay Anycast
            if (b0 == 192 && b1 == 88 && (bytes[2] & 0xFF) == 99) return true;

            // 198.18.0.0/15 — Benchmarking
            if (b0 == 198 && (b1 == 18 || b1 == 19)) return true;

            // 198.51.100.0/24 — Documentation (TEST-NET-2)
            if (b0 == 198 && b1 == 51 && (bytes[2] & 0xFF) == 100) return true;

            // 203.0.113.0/24 — Documentation (TEST-NET-3)
            if (b0 == 203 && b1 == 0 && (bytes[2] & 0xFF) == 113) return true;

            // 240.0.0.0/4 — Reserved for future use
            if (b0 >= 240) return true;
        }

        return false;
    }
}
