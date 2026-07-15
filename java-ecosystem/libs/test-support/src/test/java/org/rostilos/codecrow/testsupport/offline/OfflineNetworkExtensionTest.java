package org.rostilos.codecrow.testsupport.offline;

import okhttp3.OkHttpClient;
import okhttp3.Request;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.RegisterExtension;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Path;
import java.net.InetAddress;
import java.net.Socket;
import java.security.AllPermission;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.assertj.core.api.Assertions.catchThrowable;

class OfflineNetworkExtensionTest {

    @RegisterExtension
    final OfflineNetworkExtension offlineNetwork = new OfflineNetworkExtension();

    @Test
    void interceptsUnmodifiedResolutionRawSocketsAndOkHttpBeforeDns() {
        assertThatThrownBy(() -> InetAddress.getByName("dns-must-not-run.invalid"))
                .isInstanceOf(UnexpectedExternalCall.class);
        assertThatThrownBy(() -> new Socket("socket-must-not-run.invalid", 443))
                .isInstanceOf(UnexpectedExternalCall.class);

        Throwable okHttpFailure = catchThrowable(() -> new OkHttpClient()
                .newCall(new Request.Builder()
                        .url("https://okhttp-must-not-run.invalid/v1/chat")
                        .build())
                .execute());

        assertThat(rootCause(okHttpFailure)).isInstanceOf(UnexpectedExternalCall.class);
        assertThat(offlineNetwork.ledger().entries()).hasSize(3).allSatisfy(call -> {
            assertThat(call.live()).isFalse();
            assertThat(call.outcome()).isEqualTo("blocked");
            assertThat(call.phase()).isEqualTo("PRE_DNS");
            assertThat(call.simulated()).isFalse();
        });
        offlineNetwork.ledger().assertZeroLiveCalls();
        assertThat(offlineNetwork.ledger().entries())
                .extracting(ExternalCall::target)
                .containsExactly(
                        "dns-must-not-run.invalid:0",
                        "socket-must-not-run.invalid:0",
                        "okhttp-must-not-run.invalid:0"
                );
        offlineNetwork.ledger().entries().forEach(call -> offlineNetwork.acknowledgeBlockedCall(
                call, "network", "resolve", "PRE_DNS", call.target()
        ));
    }

    @Test
    @SuppressWarnings("removal")
    void allowsOnlyDnsAndConnectChecksForTheExactLeasedEndpoint() {
        SecurityManager installed = System.getSecurityManager();

        try (NetworkDenyGuard.NetworkLease ignored = offlineNetwork.allowLoopback("127.0.0.1", 15432)) {
            installed.checkPermission(new AllPermission());
            installed.checkPermission(new AllPermission(), new Object());
            installed.checkPermission(new RuntimePermission("ordinary-test-permission"));
            installed.checkConnect("127.0.0.1", -1);
            installed.checkConnect("127.0.0.1", 15432);
            installed.checkConnect("127.0.0.1", 15432, new Object());

            assertThatThrownBy(() -> installed.checkConnect("127.0.0.1", 15433))
                    .isInstanceOf(UnexpectedExternalCall.class)
                    .hasMessageContaining("127.0.0.1:15433");
            assertThatThrownBy(() -> installed.checkConnect("::1", -1))
                    .isInstanceOf(UnexpectedExternalCall.class);
        }

        assertThatThrownBy(() -> installed.checkConnect("127.0.0.1", -1))
                .isInstanceOf(UnexpectedExternalCall.class);
        assertThat(offlineNetwork.ledger().entries()).satisfiesExactly(
                call -> acknowledgeBlockedCall(
                        call, "network", "connect", "PRE_SOCKET", "127.0.0.1:15433"
                ),
                call -> acknowledgeBlockedCall(
                        call, "network", "resolve", "PRE_DNS", "[::1]:0"
                ),
                call -> acknowledgeBlockedCall(
                        call, "network", "resolve", "PRE_DNS", "127.0.0.1:0"
                )
        );
    }

    @Test
    void deniesRuntimeExecAndProcessBuilderBeforeEitherChildCanCreateAMarker(
            @TempDir Path temporaryDirectory
    ) {
        Path runtimeMarker = temporaryDirectory.resolve("runtime-exec-ran");
        Path processBuilderMarker = temporaryDirectory.resolve("process-builder-ran");

        UnexpectedExternalCall runtimeDenial = assertThrows(
                UnexpectedExternalCall.class,
                () -> Runtime.getRuntime().exec(new String[]{
                        "/usr/bin/touch", runtimeMarker.toString()
                })
        );
        UnexpectedExternalCall processBuilderDenial = assertThrows(
                UnexpectedExternalCall.class,
                () -> new ProcessBuilder(
                        "/usr/bin/touch", processBuilderMarker.toString()
                ).start()
        );

        assertThat(List.of(runtimeDenial, processBuilderDenial)).allSatisfy(denial -> {
            assertThat(denial.getMessage())
                    .isEqualTo("unregistered outbound target: <redacted-target>")
                    .doesNotContain("/usr/bin/touch")
                    .doesNotContain(temporaryDirectory.toString());
            assertThat(denial.call().target()).isEqualTo("<redacted-target>");
        });

        assertThat(runtimeMarker).doesNotExist();
        assertThat(processBuilderMarker).doesNotExist();
        assertThat(offlineNetwork.ledger().entries()).satisfiesExactly(
                call -> {
                    assertThat(call).isSameAs(runtimeDenial.call());
                    assertBlockedCall(
                            call, "process", "exec", "PRE_EXEC", "<redacted-target>"
                    );
                },
                call -> {
                    assertThat(call).isSameAs(processBuilderDenial.call());
                    assertBlockedCall(
                            call, "process", "exec", "PRE_EXEC", "<redacted-target>"
                    );
                }
        );
    }

    @Test
    @SuppressWarnings("removal")
    void deniedSecurityManagerTamperingCannotPrecedeDnsSocketOrProcessEscape(
            @TempDir Path temporaryDirectory
    ) throws Exception {
        SecurityManager installed = System.getSecurityManager();
        SecurityManager permissiveReplacement = new SecurityManager() {
            @Override
            public void checkPermission(java.security.Permission permission) {
                // This manager would permit every escape if replacement succeeded.
            }
        };
        Path processMarker = temporaryDirectory.resolve("tamper-escape-ran");

        assertThatThrownBy(() -> System.setSecurityManager(null))
                .isInstanceOf(SecurityException.class)
                .hasMessageContaining("denies untrusted");
        assertThatThrownBy(() -> System.setSecurityManager(permissiveReplacement))
                .isInstanceOf(SecurityException.class)
                .hasMessageContaining("denies untrusted");
        assertThat(System.getSecurityManager()).isSameAs(installed);

        assertThatThrownBy(() -> InetAddress.getByName("tamper-dns.invalid"))
                .isInstanceOf(UnexpectedExternalCall.class);
        InetAddress literalLoopback = InetAddress.getByAddress(new byte[]{127, 0, 0, 1});
        assertThatThrownBy(() -> new Socket(literalLoopback, 9))
                .isInstanceOf(UnexpectedExternalCall.class);
        assertThatThrownBy(() -> new ProcessBuilder(
                "/usr/bin/touch", processMarker.toString()
        ).start()).isInstanceOf(UnexpectedExternalCall.class);

        assertThat(processMarker).doesNotExist();
        assertThat(offlineNetwork.ledger().entries()).satisfiesExactly(
                call -> assertBlockedCall(
                        call, "network", "resolve", "PRE_DNS", "tamper-dns.invalid:0"
                ),
                call -> assertBlockedCall(
                        call, "network", "connect", "PRE_SOCKET", "127.0.0.1:9"
                ),
                call -> assertBlockedCall(
                        call, "process", "exec", "PRE_EXEC", "<redacted-target>"
                )
        );
    }

    private void assertBlockedCall(
            ExternalCall call,
            String boundary,
            String operation,
            String phase,
            String target
    ) {
        assertThat(call.boundary()).isEqualTo(boundary);
        assertThat(call.operation()).isEqualTo(operation);
        assertThat(call.outcome()).isEqualTo("blocked");
        assertThat(call.phase()).isEqualTo(phase);
        assertThat(call.target()).isEqualTo(target);
        assertThat(call.live()).isFalse();
        assertThat(call.simulated()).isFalse();
        offlineNetwork.acknowledgeBlockedCall(call, boundary, operation, phase, target);
    }

    private void acknowledgeBlockedCall(
            ExternalCall call,
            String boundary,
            String operation,
            String phase,
            String target
    ) {
        assertBlockedCall(call, boundary, operation, phase, target);
    }

    private static Throwable rootCause(Throwable failure) {
        Throwable current = failure;
        while (current.getCause() != null) {
            current = current.getCause();
        }
        return current;
    }
}
