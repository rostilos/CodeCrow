package org.rostilos.codecrow.testsupport.offline;

import org.junit.jupiter.api.extension.AfterEachCallback;
import org.junit.jupiter.api.extension.BeforeEachCallback;
import org.junit.jupiter.api.extension.ExtensionContext;

import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.List;
import java.util.Locale;
import java.util.Objects;
import java.util.Optional;
import java.util.regex.Pattern;

/**
 * JUnit 5 extension that automatically installs and restores the process-wide
 * offline boundary around each test. Register it with {@code @RegisterExtension}
 * before constructing application clients.
 */
public final class OfflineNetworkExtension implements BeforeEachCallback, AfterEachCallback {

    static final String LEDGER_DIRECTORY_ENV = "CODECROW_EXTERNAL_CALL_LEDGER_DIR";
    private static final Pattern UNSAFE_FILENAME = Pattern.compile("[^a-z0-9._-]+");
    private static final int FILENAME_PREFIX_LIMIT = 96;

    private final LedgerDirectoryResolver ledgerDirectoryResolver;
    private final LedgerExporter ledgerExporter;
    private ExternalCallLedger ledger;
    private OfflineNetworkBoundary boundary;

    public OfflineNetworkExtension() {
        this(
                () -> configuredLedgerDirectory(System.getenv(LEDGER_DIRECTORY_ENV)),
                ExternalCallLedger::writeJson
        );
    }

    OfflineNetworkExtension(
            LedgerDirectoryResolver ledgerDirectoryResolver,
            LedgerExporter ledgerExporter
    ) {
        this.ledgerDirectoryResolver = Objects.requireNonNull(
                ledgerDirectoryResolver, "ledgerDirectoryResolver"
        );
        this.ledgerExporter = Objects.requireNonNull(ledgerExporter, "ledgerExporter");
    }

    @Override
    public void beforeEach(ExtensionContext context) {
        ledger = new ExternalCallLedger();
        boundary = OfflineNetworkBoundary.install(ledger);
    }

    @Override
    public void afterEach(ExtensionContext context) throws Exception {
        Throwable primaryFailure = context.getExecutionException().orElse(null);
        List<Throwable> teardownFailures = new ArrayList<>();
        capture(teardownFailures, ledger::assertZeroLiveCalls);
        capture(teardownFailures, ledger::assertNoUnacknowledgedBlockedCalls);
        OfflineNetworkBoundary installedBoundary = boundary;
        capture(teardownFailures, installedBoundary::close);
        boundary = null;
        capture(teardownFailures, () -> {
            Optional<Path> directory = ledgerDirectoryResolver.resolve();
            if (directory.isPresent()) {
                ledgerExporter.export(
                        ledger,
                        directory.get().resolve(ledgerFilename(context))
                );
            }
        });

        if (primaryFailure != null) {
            teardownFailures.forEach(primaryFailure::addSuppressed);
            return;
        }
        if (teardownFailures.isEmpty()) {
            return;
        }
        Throwable firstFailure = teardownFailures.remove(0);
        teardownFailures.forEach(firstFailure::addSuppressed);
        if (firstFailure instanceof Error error) {
            throw error;
        }
        throw (Exception) firstFailure;
    }

    public ExternalCallLedger ledger() {
        return Objects.requireNonNull(ledger, "the extension has not started");
    }

    public NetworkDenyGuard.NetworkLease allowLoopback(String host, int port) {
        return Objects.requireNonNull(boundary, "the extension has not started")
                .allowLoopback(host, port);
    }

    public void acknowledgeBlockedCall(
            ExternalCall call,
            String boundary,
            String operation,
            String phase,
            String target
    ) {
        ledger().acknowledgeBlocked(call, boundary, operation, phase, target);
    }

    static Optional<Path> configuredLedgerDirectory(String configured) {
        return configured == null || configured.isBlank()
                ? Optional.empty()
                : Optional.of(Path.of(configured));
    }

    static String ledgerFilename(ExtensionContext context) {
        String uniqueId = Objects.requireNonNull(context.getUniqueId(), "JUnit unique id");
        String sanitized = UNSAFE_FILENAME.matcher(uniqueId.toLowerCase(Locale.ROOT))
                .replaceAll("-");
        String prefix = sanitized.substring(
                0, Math.min(sanitized.length(), FILENAME_PREFIX_LIMIT)
        );
        return prefix + "-" + digest(uniqueId, "SHA-256") + ".json";
    }

    static String digest(String value, String algorithm) {
        try {
            MessageDigest digest = MessageDigest.getInstance(algorithm);
            return HexFormat.of().formatHex(digest.digest(value.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException failure) {
            throw new IllegalStateException("required ledger filename digest is unavailable", failure);
        }
    }

    private static void capture(List<Throwable> failures, TeardownAction action) {
        try {
            action.run();
        } catch (Throwable failure) {
            failures.add(failure);
        }
    }

    @FunctionalInterface
    interface LedgerDirectoryResolver {
        Optional<Path> resolve() throws Exception;
    }

    @FunctionalInterface
    interface LedgerExporter {
        void export(ExternalCallLedger ledger, Path destination) throws Exception;
    }

    @FunctionalInterface
    private interface TeardownAction {
        void run() throws Exception;
    }
}
