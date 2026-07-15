package org.rostilos.codecrow.testsupport.offline;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtensionContext;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.net.InetAddress;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Optional;
import java.util.concurrent.atomic.AtomicBoolean;

import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class OfflineNetworkExtensionLifecycleTest {

    @TempDir
    Path temporaryDirectory;

    @Test
    @SuppressWarnings("removal")
    void leakedLoopbackLeaseFailsTeardownWithExactDiagnosticAndRestoresBoundary() {
        OfflineNetworkExtension extension = nonExportingExtension();
        ExtensionContext context = context(
                "[engine:junit-jupiter]/[method:leaked-loopback-lease()]", null
        );
        extension.beforeEach(context);
        NetworkDenyGuard.NetworkLease leaked = extension.allowLoopback("127.0.0.1", 15432);

        try {
            assertThatThrownBy(() -> extension.afterEach(context))
                    .isInstanceOf(AssertionError.class)
                    .hasMessage(
                            "leaked loopback endpoint lease(s): 127.0.0.1:15432 (count=1)"
                    );
        } finally {
            leaked.close();
        }

        assertThat(System.getSecurityManager()).isNull();
    }

    @Test
    @SuppressWarnings("removal")
    void teardownFailsOnALiveCallAndStillRestoresTheSecurityManager() {
        AtomicBoolean exporterCalled = new AtomicBoolean();
        OfflineNetworkExtension extension = new OfflineNetworkExtension(
                Optional::empty,
                (ledger, destination) -> exporterCalled.set(true)
        );
        ExtensionContext context = context("[engine:junit-jupiter]/[method:live()]", null);
        extension.beforeEach(context);
        NetworkDenyGuard.NetworkLease leaked = extension.allowLoopback("127.0.0.1", 15433);
        extension.ledger().record(
                "llm", true, "invoke", "response", "PRE_SOCKET", false, "api.openai.invalid:443"
        );

        assertThatThrownBy(() -> extension.afterEach(context))
                .isInstanceOf(AssertionError.class)
                .hasMessageContaining("recorded 1")
                .satisfies(failure -> assertThat(failure.getSuppressed())
                        .singleElement()
                        .satisfies(suppressed -> assertThat(suppressed)
                                .isInstanceOf(AssertionError.class)
                                .hasMessage(
                                        "leaked loopback endpoint lease(s): "
                                                + "127.0.0.1:15433 (count=1)"
                                )));

        leaked.close();
        assertThat(exporterCalled).isFalse();
        assertThat(System.getSecurityManager()).isNull();
    }

    @Test
    @SuppressWarnings("removal")
    void swallowedBlockedResolutionFailsTeardownWhenExactCallIsNotAcknowledged() {
        OfflineNetworkExtension extension = nonExportingExtension();
        ExtensionContext context = context(
                "[engine:junit-jupiter]/[method:swallowed-block()]", null
        );
        extension.beforeEach(context);

        UnexpectedExternalCall denial = assertThrows(
                UnexpectedExternalCall.class,
                () -> InetAddress.getByName("swallowed-denial.invalid")
        );
        ExternalCall blocked = denial.call();
        assertThat(extension.ledger().entries()).singleElement().isSameAs(blocked);
        assertThat(blocked).satisfies(call -> {
            assertThat(call.outcome()).isEqualTo("blocked");
            assertThat(call.phase()).isEqualTo("PRE_DNS");
        });
        assertThatThrownBy(() -> extension.acknowledgeBlockedCall(
                blocked, "network", "connect", "PRE_DNS", "<redacted-target>"
        )).isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("does not match");

        assertThatThrownBy(() -> extension.afterEach(context))
                .isInstanceOf(AssertionError.class)
                .hasMessageContaining("unacknowledged")
                .hasMessageContaining("sequence(s): 1");
        assertThat(System.getSecurityManager()).isNull();
    }

    @Test
    @SuppressWarnings("removal")
    void blockRecordedAfterExactAcknowledgementFailsTeardownWithLateSequence() {
        OfflineNetworkExtension extension = nonExportingExtension();
        ExtensionContext context = context(
                "[engine:junit-jupiter]/[method:late-block()]", null
        );
        extension.beforeEach(context);

        assertThatThrownBy(() -> InetAddress.getByName("first-denial.invalid"))
                .isInstanceOf(UnexpectedExternalCall.class);
        ExternalCall first = extension.ledger().entries().get(0);
        extension.acknowledgeBlockedCall(
                first, "network", "resolve", "PRE_DNS", "first-denial.invalid"
        );
        assertThatThrownBy(() -> InetAddress.getByName("late-denial.invalid"))
                .isInstanceOf(UnexpectedExternalCall.class);
        assertThat(extension.ledger().entries()).hasSize(2).allSatisfy(call -> {
            assertThat(call.outcome()).isEqualTo("blocked");
            assertThat(call.phase()).isEqualTo("PRE_DNS");
        });

        assertThatThrownBy(() -> extension.afterEach(context))
                .isInstanceOf(AssertionError.class)
                .hasMessageContaining("unacknowledged")
                .hasMessageContaining("sequence(s): 2");
        assertThat(System.getSecurityManager()).isNull();
    }

    @Test
    void exportsCanonicalBlockedAndSimulatedCallsToAContextUniqueDocument() throws Exception {
        OfflineNetworkExtension extension = exportingExtension();
        ExtensionContext context = context(
                "[engine:junit-jupiter]/[class:LedgerExport]/[method:blocked-and-simulated()]",
                null
        );
        extension.beforeEach(context);
        ExternalCall blocked = extension.ledger().record(
                "network", false, "connect", "blocked", "PRE_DNS", false, "api.invalid:443"
        );
        extension.ledger().record(
                "llm", false, "invoke", "structured", "SIMULATED", true, "fake-llm:24117"
        );
        extension.acknowledgeBlockedCall(
                blocked, "network", "connect", "PRE_DNS", "api.invalid:443"
        );

        extension.afterEach(context);

        Path exported = temporaryDirectory.resolve(OfflineNetworkExtension.ledgerFilename(context));
        assertThat(exported).isRegularFile();
        assertThat(Files.size(exported)).isPositive();
        ExternalCallLedgerDocument document = new ObjectMapper().readValue(
                exported.toFile(), ExternalCallLedgerDocument.class
        );
        assertThat(document.liveCallCount()).isZero();
        assertThat(document.simulatedCallCount()).isEqualTo(1);
        assertThat(document.calls()).extracting(ExternalCall::phase)
                .containsExactly("PRE_DNS", "SIMULATED");
    }

    @Test
    @SuppressWarnings("removal")
    void primaryFailureKeepsLiveAssertionAndExportFailureAsSuppressed() throws Exception {
        IOException exportFailure = new IOException("scripted ledger export failure");
        AtomicBoolean exportAttempted = new AtomicBoolean();
        OfflineNetworkExtension extension = new OfflineNetworkExtension(
                () -> Optional.of(temporaryDirectory),
                (ledger, destination) -> {
                    exportAttempted.set(true);
                    throw exportFailure;
                }
        );
        IllegalStateException primaryFailure = new IllegalStateException("primary test failure");
        ExtensionContext context = context(
                "[engine:junit-jupiter]/[method:primary-failure()]", primaryFailure
        );
        extension.beforeEach(context);
        NetworkDenyGuard.NetworkLease leaked = extension.allowLoopback("::1", 15434);
        extension.ledger().record(
                "llm", true, "invoke", "response", "PRE_SOCKET", false, "api.invalid:443"
        );

        extension.afterEach(context);

        assertThat(exportAttempted).isTrue();
        assertThat(primaryFailure.getSuppressed())
                .hasSize(3)
                .anySatisfy(failure -> assertThat(failure).isInstanceOf(AssertionError.class))
                .anySatisfy(failure -> assertThat(failure)
                        .hasMessage(
                                "leaked loopback endpoint lease(s): [::1]:15434 (count=1)"
                        ))
                .anySatisfy(failure -> assertThat(failure).isSameAs(exportFailure));
        leaked.close();
        assertThat(System.getSecurityManager()).isNull();
    }

    @Test
    @SuppressWarnings("removal")
    void exportFailureIsReportedAfterBoundaryCleanupAndLeavesNoArtifact() {
        IOException exportFailure = new IOException("scripted ledger export failure");
        OfflineNetworkExtension extension = new OfflineNetworkExtension(
                () -> Optional.of(temporaryDirectory),
                (ledger, destination) -> {
                    throw exportFailure;
                }
        );
        ExtensionContext context = context(
                "[engine:junit-jupiter]/[method:export-failure()]", null
        );
        extension.beforeEach(context);

        assertThatThrownBy(() -> extension.afterEach(context)).isSameAs(exportFailure);

        assertThat(temporaryDirectory).isEmptyDirectory();
        assertThat(System.getSecurityManager()).isNull();
    }

    @Test
    void resolvesConfiguredDirectoryAndBuildsStableCollisionResistantFilenames() {
        assertThat(OfflineNetworkExtension.configuredLedgerDirectory(null)).isEmpty();
        assertThat(OfflineNetworkExtension.configuredLedgerDirectory("  ")).isEmpty();
        assertThat(OfflineNetworkExtension.configuredLedgerDirectory(temporaryDirectory.toString()))
                .contains(temporaryDirectory);

        ExtensionContext slashContext = context(
                "[engine:junit-jupiter]/[method:same/prefix()]", null
        );
        ExtensionContext questionContext = context(
                "[engine:junit-jupiter]/[method:same?prefix()]", null
        );
        String slashName = OfflineNetworkExtension.ledgerFilename(slashContext);
        assertThat(OfflineNetworkExtension.ledgerFilename(slashContext)).isEqualTo(slashName);
        assertThat(OfflineNetworkExtension.ledgerFilename(questionContext)).isNotEqualTo(slashName);
        assertThat(slashName).matches("[a-z0-9._-]+-[0-9a-f]{64}\\.json");

        ExtensionContext longContext = context("x".repeat(200), null);
        assertThat(OfflineNetworkExtension.ledgerFilename(longContext)).hasSize(166);
        assertThatThrownBy(() -> OfflineNetworkExtension.digest("context", "missing-digest"))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("digest is unavailable");
    }

    private OfflineNetworkExtension exportingExtension() {
        return new OfflineNetworkExtension(
                () -> Optional.of(temporaryDirectory),
                ExternalCallLedger::writeJson
        );
    }

    private static OfflineNetworkExtension nonExportingExtension() {
        return new OfflineNetworkExtension(
                Optional::empty,
                (ledger, destination) -> {
                    throw new AssertionError("exporter must remain disabled");
                }
        );
    }

    private static ExtensionContext context(String uniqueId, Throwable executionFailure) {
        ExtensionContext context = mock(ExtensionContext.class);
        when(context.getUniqueId()).thenReturn(uniqueId);
        when(context.getExecutionException()).thenReturn(Optional.ofNullable(executionFailure));
        return context;
    }
}
