package org.rostilos.codecrow.testsupport.offline;

import com.fasterxml.jackson.databind.ObjectMapper;

import java.io.IOException;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.CopyOption;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.net.URI;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Objects;
import java.util.Set;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicLong;
import java.util.regex.Pattern;

/**
 * Thread-safe append-only ledger for simulated, blocked, and live boundary calls.
 * Values are redacted before they enter memory so snapshots and diagnostics cannot
 * expose common credential formats.
 */
public final class ExternalCallLedger {

    private static final Pattern SAFE_HOST = Pattern.compile(
            "(?i)^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$"
    );
    private static final Pattern BRACKETED_IPV6 = Pattern.compile("(?i)^\\[[0-9a-f:]+]$");
    private static final Pattern UNBRACKETED_IPV6 = Pattern.compile("(?i)^[0-9a-f:]+$");
    private static final Pattern HOST_PORT = Pattern.compile("^(.+):([0-9]{1,5})$");
    private static final Pattern BOUNDARY = Pattern.compile("^[a-z][a-z0-9_-]*$");
    private static final Pattern OPERATION = Pattern.compile("^[a-z][a-z0-9_.-]*$");
    private static final Pattern OUTCOME = Pattern.compile("^[a-z][a-z0-9_-]*$");
    private static final Set<String> PHASES = Set.of(
            "PRE_DNS", "PRE_SOCKET", "PRE_EXEC", "SIMULATED"
    );

    private final AtomicLong nextSequence = new AtomicLong(1);
    private final CopyOnWriteArrayList<ExternalCall> entries = new CopyOnWriteArrayList<>();
    private final Set<Long> acknowledgedBlockedSequences = new HashSet<>();
    private final Object appendLock = new Object();
    private final ObjectMapper objectMapper;
    private final FileMover fileMover;

    public ExternalCallLedger() {
        this(new ObjectMapper(), Files::move);
    }

    ExternalCallLedger(ObjectMapper objectMapper, FileMover fileMover) {
        this.objectMapper = Objects.requireNonNull(objectMapper, "objectMapper");
        this.fileMover = Objects.requireNonNull(fileMover, "fileMover");
    }

    public ExternalCall record(
            String boundary,
            boolean live,
            String operation,
            String outcome,
            String phase,
            boolean simulated,
            String target
    ) {
        String canonicalBoundary = canonicalIdentifier(boundary, BOUNDARY, "boundary");
        String canonicalOperation = canonicalIdentifier(operation, OPERATION, "operation");
        String canonicalOutcome = canonicalIdentifier(outcome, OUTCOME, "outcome");
        String canonicalCallPhase = canonicalPhase(phase);
        String redactedTarget = redactTarget(target, canonicalCallPhase);
        if (live && simulated) {
            throw new IllegalArgumentException("a call cannot be both live and simulated");
        }
        synchronized (appendLock) {
            ExternalCall entry = new ExternalCall(
                    canonicalBoundary,
                    live,
                    canonicalOperation,
                    canonicalOutcome,
                    canonicalCallPhase,
                    nextSequence.getAndIncrement(),
                    simulated,
                    redactedTarget
            );
            entries.add(entry);
            return entry;
        }
    }

    public List<ExternalCall> entries() {
        return List.copyOf(entries);
    }

    public long liveCallCount() {
        return entries.stream().filter(ExternalCall::live).count();
    }

    public long simulatedCallCount() {
        return entries.stream().filter(ExternalCall::simulated).count();
    }

    public ExternalCallLedgerDocument snapshot() {
        synchronized (appendLock) {
            List<ExternalCall> calls = List.copyOf(entries);
            return new ExternalCallLedgerDocument(
                    "1.0",
                    calls.stream().filter(ExternalCall::live).count(),
                    calls.stream().filter(ExternalCall::simulated).count(),
                    calls
            );
        }
    }

    public void assertZeroLiveCalls() {
        long liveCalls = liveCallCount();
        if (liveCalls != 0) {
            throw new AssertionError("expected zero live external calls but recorded " + liveCalls);
        }
    }

    public void acknowledgeBlocked(
            ExternalCall call,
            String boundary,
            String operation,
            String phase,
            String target
    ) {
        Objects.requireNonNull(call, "call");
        String expectedBoundary = canonicalIdentifier(boundary, BOUNDARY, "boundary");
        String expectedOperation = canonicalIdentifier(operation, OPERATION, "operation");
        String expectedPhase = canonicalPhase(phase);
        String expectedTarget = redactTarget(target, expectedPhase);
        synchronized (appendLock) {
            boolean recordedByThisLedger = entries.stream().anyMatch(entry -> entry == call);
            if (!recordedByThisLedger || !"blocked".equals(call.outcome())) {
                throw new IllegalArgumentException("only a recorded blocked call can be acknowledged");
            }
            List<String> actual = List.of(
                    call.boundary(), call.operation(), call.phase(), call.target()
            );
            List<String> expected = List.of(
                    expectedBoundary, expectedOperation, expectedPhase, expectedTarget
            );
            if (!actual.equals(expected)) {
                throw new IllegalArgumentException(
                        "blocked-call acknowledgement does not match the expected call"
                );
            }
            acknowledgedBlockedSequences.add(call.sequence());
        }
    }

    public void assertNoUnacknowledgedBlockedCalls() {
        List<Long> unacknowledged;
        synchronized (appendLock) {
            unacknowledged = entries.stream()
                    .filter(call -> "blocked".equals(call.outcome()))
                    .map(ExternalCall::sequence)
                    .filter(sequence -> !acknowledgedBlockedSequences.contains(sequence))
                    .toList();
        }
        if (!unacknowledged.isEmpty()) {
            throw new AssertionError(
                    "unacknowledged blocked external call sequence(s): "
                            + String.join(
                                    ", ",
                                    unacknowledged.stream()
                                            .map(sequence -> Long.toString(sequence))
                                            .toList()
                            )
            );
        }
    }

    /**
     * Writes the canonical versioned JSON document. In-memory values are already redacted.
     */
    public void writeJson(Path destination) throws IOException {
        Path absoluteDestination = destination.toAbsolutePath();
        Path parent = absoluteDestination.getParent();
        Files.createDirectories(parent);
        Path temporary = Files.createTempFile(
                parent,
                ".external-call-ledger-" + absoluteDestination.getFileName() + "-",
                ".tmp"
        );
        try {
            Files.write(temporary, canonicalJsonBytes());
            try {
                fileMover.move(
                        temporary,
                        absoluteDestination,
                        StandardCopyOption.ATOMIC_MOVE,
                        StandardCopyOption.REPLACE_EXISTING
                );
            } catch (AtomicMoveNotSupportedException unsupported) {
                fileMover.move(
                        temporary,
                        absoluteDestination,
                        StandardCopyOption.REPLACE_EXISTING
                );
            }
        } finally {
            Files.deleteIfExists(temporary);
        }
    }

    /**
     * Returns the canonical versioned JSON document for callers that must write through an
     * already-open, nofollow file descriptor.
     */
    public byte[] canonicalJsonBytes() throws IOException {
        return objectMapper.writeValueAsBytes(snapshot());
    }

    @FunctionalInterface
    interface FileMover {
        void move(Path source, Path destination, CopyOption... options) throws IOException;
    }

    private static String canonicalIdentifier(String value, Pattern pattern, String field) {
        String canonical = requiredText(value, field).toLowerCase(Locale.ROOT);
        if (!pattern.matcher(canonical).matches()) {
            throw new IllegalArgumentException("invalid external-call " + field);
        }
        return canonical;
    }

    private static String canonicalPhase(String phase) {
        String canonical = requiredText(phase, "phase").toUpperCase(Locale.ROOT);
        if (!PHASES.contains(canonical)) {
            throw new IllegalArgumentException("unsupported external-call phase: " + phase);
        }
        return canonical;
    }

    private static String redactTarget(String target, String phase) {
        String canonicalTarget = requiredText(target, "target");
        if ("PRE_DNS".equals(phase)) {
            String dnsHost = canonicalHost(canonicalTarget);
            if (dnsHost != null) {
                String authority = dnsHost;
                if (dnsHost.indexOf(':') >= 0 && !dnsHost.startsWith("[")) {
                    authority = "[" + dnsHost + "]";
                }
                return authority + ":0";
            }
        }
        String canonicalUri = canonicalUri(canonicalTarget);
        if (canonicalUri != null) {
            return canonicalUri;
        }
        String canonicalHostPort = canonicalHostPort(canonicalTarget);
        return canonicalHostPort == null ? "<redacted-target>" : canonicalHostPort;
    }

    private static String canonicalUri(String target) {
        try {
            URI uri = URI.create(target);
            if (uri.getScheme() != null && uri.getHost() != null) {
                String host = canonicalHost(uri.getHost());
                int port = uri.getPort();
                if (host != null && port <= 65535) {
                    String endpoint = uri.getScheme().toLowerCase(Locale.ROOT) + "://" + host;
                    return port == -1 ? endpoint : endpoint + ":" + port;
                }
            }
        } catch (IllegalArgumentException ignored) {
            // A malformed value is handled by the fail-closed fallback below.
        }
        return null;
    }

    private static String canonicalHostPort(String target) {
        var match = HOST_PORT.matcher(target);
        if (!match.matches()) {
            return null;
        }
        String host = canonicalHost(match.group(1));
        String port = canonicalPort(match.group(2));
        if (host == null || port == null) {
            return null;
        }
        return host + ":" + port;
    }

    private static String canonicalHost(String host) {
        if (SAFE_HOST.matcher(host).matches()
                || BRACKETED_IPV6.matcher(host).matches()
                || (host.indexOf(':') >= 0 && UNBRACKETED_IPV6.matcher(host).matches())) {
            return host.toLowerCase(Locale.ROOT);
        }
        return null;
    }

    private static String canonicalPort(String port) {
        int numeric = Integer.parseInt(port);
        return numeric <= 65535 ? Integer.toString(numeric) : null;
    }

    private static String requiredText(String value, String field) {
        String required = Objects.requireNonNull(value, field);
        if (required.isBlank()) {
            throw new IllegalArgumentException("external-call " + field + " must not be blank");
        }
        return required.strip();
    }
}
