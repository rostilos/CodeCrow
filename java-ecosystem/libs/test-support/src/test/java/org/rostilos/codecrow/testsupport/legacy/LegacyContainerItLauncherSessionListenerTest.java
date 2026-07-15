package org.rostilos.codecrow.testsupport.legacy;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.mockito.MockedConstruction;
import org.mockito.MockedStatic;
import org.rostilos.codecrow.testsupport.offline.ExternalCallLedger;
import org.rostilos.codecrow.testsupport.offline.OfflineNetworkBoundary;

import java.io.IOException;
import java.lang.reflect.Constructor;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.net.Socket;
import java.net.SocketAddress;
import java.nio.file.Path;
import java.util.Optional;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.BiFunction;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.mockConstruction;
import static org.mockito.Mockito.mockStatic;
import static org.mockito.Mockito.when;

class LegacyContainerItLauncherSessionListenerTest {

    @TempDir
    Path ledgerDirectory;

    @Test
    void inactiveListenerDoesNotInspectVisibilityOrCreateRuntime() {
        AtomicInteger visibilityChecks = new AtomicInteger();
        AtomicInteger runtimeCreations = new AtomicInteger();
        LegacyContainerItLauncherSessionListener listener = new LegacyContainerItLauncherSessionListener(
                () -> Optional.empty(),
                () -> visibilityChecks.incrementAndGet(),
                activation -> {
                    runtimeCreations.incrementAndGet();
                    throw new AssertionError("must not create runtime");
                }
        );

        listener.launcherSessionOpened(null);
        listener.launcherSessionClosed(null);

        assertThat(visibilityChecks).hasValue(0);
        assertThat(runtimeCreations).hasValue(0);
    }

    @Test
    void activeListenerChecksVisibilityAndOwnsOneOpenCloseLifecycle() {
        LegacyContainerItContract.Activation activation = queueActivation();
        AtomicInteger visibilityChecks = new AtomicInteger();
        AtomicReference<String> events = new AtomicReference<>("");
        LegacyContainerItLauncherSessionListener listener = new LegacyContainerItLauncherSessionListener(
                () -> Optional.of(activation),
                () -> visibilityChecks.incrementAndGet(),
                ignored -> new LegacyContainerItLauncherSessionListener.Lifecycle() {
                    @Override
                    public void open() {
                        events.updateAndGet(value -> value + "open;");
                    }

                    @Override
                    public void closeForProcessExit() {
                        events.updateAndGet(value -> value + "close;");
                    }
                }
        );

        listener.launcherSessionOpened(null);
        assertThatThrownBy(() -> listener.launcherSessionOpened(null))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("already opened");
        listener.launcherSessionClosed(null);
        listener.launcherSessionClosed(null);

        assertThat(visibilityChecks).hasValue(1);
        assertThat(events).hasValue("open;close;");
    }

    @Test
    void visibilityFailurePreventsRuntimeConstructionAndCannotBeRetried() {
        AtomicInteger creations = new AtomicInteger();
        IllegalStateException visibilityFailure = new IllegalStateException("socket visible");
        LegacyContainerItLauncherSessionListener listener =
                new LegacyContainerItLauncherSessionListener(
                        () -> Optional.of(queueActivation()),
                        () -> {
                            throw visibilityFailure;
                        },
                        ignored -> {
                            creations.incrementAndGet();
                            throw new AssertionError("must not construct runtime");
                        }
                );

        assertThatThrownBy(() -> listener.launcherSessionOpened(null))
                .isSameAs(visibilityFailure);
        assertThatThrownBy(() -> listener.launcherSessionOpened(null))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("already opened");
        assertThat(creations).hasValue(0);
    }

    @Test
    void lifecycleOpenFailureIsNotPublishedForClose() {
        AtomicInteger closes = new AtomicInteger();
        IllegalStateException openFailure = new IllegalStateException("open failed");
        LegacyContainerItLauncherSessionListener listener =
                new LegacyContainerItLauncherSessionListener(
                        () -> Optional.of(queueActivation()),
                        () -> { },
                        ignored -> new LegacyContainerItLauncherSessionListener.Lifecycle() {
                            @Override
                            public void open() {
                                throw openFailure;
                            }

                            @Override
                            public void closeForProcessExit() {
                                closes.incrementAndGet();
                            }
                        }
                );

        assertThatThrownBy(() -> listener.launcherSessionOpened(null)).isSameAs(openFailure);
        listener.launcherSessionClosed(null);
        assertThat(closes).hasValue(0);
    }

    @Test
    void lifecycleCloseFailureIsAttemptedOnlyOnce() {
        AtomicInteger closes = new AtomicInteger();
        IllegalStateException closeFailure = new IllegalStateException("close failed");
        LegacyContainerItLauncherSessionListener listener =
                new LegacyContainerItLauncherSessionListener(
                        () -> Optional.of(queueActivation()),
                        () -> { },
                        ignored -> new LegacyContainerItLauncherSessionListener.Lifecycle() {
                            @Override
                            public void open() {
                            }

                            @Override
                            public void closeForProcessExit() {
                                closes.incrementAndGet();
                                throw closeFailure;
                            }
                        }
                );
        listener.launcherSessionOpened(null);

        assertThatThrownBy(() -> listener.launcherSessionClosed(null)).isSameAs(closeFailure);
        listener.launcherSessionClosed(null);
        assertThat(closes).hasValue(1);
    }

    @Test
    void lifecycleAssemblyFailureClosesTheInstalledBoundary() {
        AtomicInteger closes = new AtomicInteger();
        IllegalStateException assemblyFailure = new IllegalStateException("assembly failed");
        LegacyContainerItLauncherSessionListener.LifecycleAssembly assembly =
                new LegacyContainerItLauncherSessionListener.LifecycleAssembly() {
                    @Override
                    public LegacyContainerItLauncherSessionListener.InstalledBoundary install() {
                        return closes::incrementAndGet;
                    }

                    @Override
                    public LegacyContainerItLauncherSessionListener.Lifecycle assemble(
                            LegacyContainerItContract.Activation activation,
                            LegacyContainerItLauncherSessionListener.InstalledBoundary installed
                    ) {
                        throw assemblyFailure;
                    }
                };

        assertThatThrownBy(() -> LegacyContainerItLauncherSessionListener.createLifecycle(
                queueActivation(),
                assembly
        )).isSameAs(assemblyFailure);
        assertThat(closes).hasValue(1);
    }

    @Test
    void lifecycleAssemblyPreservesFailureWhenBoundaryCleanupAlsoFails() {
        IllegalStateException assemblyFailure = new IllegalStateException("assembly failed");
        IllegalStateException cleanupFailure = new IllegalStateException("cleanup failed");
        LegacyContainerItLauncherSessionListener.LifecycleAssembly assembly =
                new LegacyContainerItLauncherSessionListener.LifecycleAssembly() {
                    @Override
                    public LegacyContainerItLauncherSessionListener.InstalledBoundary install() {
                        return () -> {
                            throw cleanupFailure;
                        };
                    }

                    @Override
                    public LegacyContainerItLauncherSessionListener.Lifecycle assemble(
                            LegacyContainerItContract.Activation activation,
                            LegacyContainerItLauncherSessionListener.InstalledBoundary installed
                    ) {
                        throw assemblyFailure;
                    }
                };

        assertThatThrownBy(() -> LegacyContainerItLauncherSessionListener.createLifecycle(
                queueActivation(),
                assembly
        )).isSameAs(assemblyFailure)
                .satisfies(failure -> assertThat(failure.getSuppressed())
                        .containsExactly(cleanupFailure));
    }

    @Test
    void checkedLifecycleFailuresAreWrappedWithoutLosingTheirCause() {
        IOException openFailure = new IOException("checked open");
        LegacyContainerItLauncherSessionListener openListener =
                new LegacyContainerItLauncherSessionListener(
                        () -> Optional.of(queueActivation()),
                        () -> { },
                        ignored -> new LegacyContainerItLauncherSessionListener.Lifecycle() {
                            @Override
                            public void open() throws Exception {
                                throw openFailure;
                            }

                            @Override
                            public void closeForProcessExit() {
                            }
                        }
                );
        assertThatThrownBy(() -> openListener.launcherSessionOpened(null))
                .isInstanceOf(IllegalStateException.class)
                .hasCause(openFailure);

        IOException closeFailure = new IOException("checked close");
        LegacyContainerItLauncherSessionListener closeListener =
                new LegacyContainerItLauncherSessionListener(
                        () -> Optional.of(queueActivation()),
                        () -> { },
                        ignored -> new LegacyContainerItLauncherSessionListener.Lifecycle() {
                            @Override
                            public void open() {
                            }

                            @Override
                            public void closeForProcessExit() throws Exception {
                                throw closeFailure;
                            }
                        }
                );
        closeListener.launcherSessionOpened(null);
        assertThatThrownBy(() -> closeListener.launcherSessionClosed(null))
                .isInstanceOf(IllegalStateException.class)
                .hasCause(closeFailure);
    }

    @Test
    void aClosedListenerCannotLaterBeOpened() {
        LegacyContainerItLauncherSessionListener listener =
                new LegacyContainerItLauncherSessionListener(
                        Optional::<LegacyContainerItContract.Activation>empty,
                        () -> { },
                        ignored -> null
                );
        listener.launcherSessionClosed(null);
        assertThatThrownBy(() -> listener.launcherSessionOpened(null))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("already closed");
    }

    @Test
    void realAssemblyCleansUpWhenInstalledBoundaryConstructionFails() {
        try (MockedStatic<OfflineNetworkBoundary> boundaries = mockStatic(
                OfflineNetworkBoundary.class
        )) {
            boundaries.when(() -> OfflineNetworkBoundary.install(any(ExternalCallLedger.class)))
                    .thenReturn(null);
            assertThatThrownBy(this::invokeRealAssemblyInstall)
                    .isInstanceOf(NullPointerException.class)
                    .satisfies(failure -> assertThat(failure.getSuppressed()).hasSize(1));
        }
    }

    @Test
    void realAssemblyClosesANonNullBoundaryAndSuppressesCleanupFailure()
            throws Throwable {
        RuntimeException assemblyFailure = new IllegalStateException("assembly failed");
        BiFunction<
                OfflineNetworkBoundary,
                ExternalCallLedger,
                LegacyContainerItLauncherSessionListener.InstalledBoundary
        > failingFactory = (boundary, ledger) -> {
            throw assemblyFailure;
        };

        OfflineNetworkBoundary cleanBoundary = mock(OfflineNetworkBoundary.class);
        try (MockedStatic<OfflineNetworkBoundary> boundaries = mockStatic(
                OfflineNetworkBoundary.class
        )) {
            boundaries.when(() -> OfflineNetworkBoundary.install(any(ExternalCallLedger.class)))
                    .thenReturn(cleanBoundary);
            Object assembly = newPrivateInstance(
                    "RealLifecycleAssembly",
                    new Class<?>[]{BiFunction.class},
                    failingFactory
            );
            assertThatThrownBy(() -> invokePrivate(
                    assembly, "install", new Class<?>[0]
            )).isSameAs(assemblyFailure);
            org.mockito.Mockito.verify(cleanBoundary).close();
        }

        RuntimeException secondFailure = new IllegalStateException("second assembly failed");
        RuntimeException cleanupFailure = new IllegalStateException("cleanup failed");
        BiFunction<
                OfflineNetworkBoundary,
                ExternalCallLedger,
                LegacyContainerItLauncherSessionListener.InstalledBoundary
        > secondFactory = (boundary, ledger) -> {
            throw secondFailure;
        };
        OfflineNetworkBoundary failingBoundary = mock(OfflineNetworkBoundary.class);
        doThrow(cleanupFailure).when(failingBoundary).close();
        try (MockedStatic<OfflineNetworkBoundary> boundaries = mockStatic(
                OfflineNetworkBoundary.class
        )) {
            boundaries.when(() -> OfflineNetworkBoundary.install(any(ExternalCallLedger.class)))
                    .thenReturn(failingBoundary);
            Object assembly = newPrivateInstance(
                    "RealLifecycleAssembly",
                    new Class<?>[]{BiFunction.class},
                    secondFactory
            );
            assertThatThrownBy(() -> invokePrivate(
                    assembly, "install", new Class<?>[0]
            )).isSameAs(secondFailure)
                    .satisfies(failure -> assertThat(failure.getSuppressed())
                            .containsExactly(cleanupFailure));
        }
    }

    @Test
    void realInstalledBoundaryAndAbortBothCloseTheirBoundary() throws Throwable {
        OfflineNetworkBoundary boundary = mock(OfflineNetworkBoundary.class);
        ExternalCallLedger ledger = mock(ExternalCallLedger.class);
        Object installed = newPrivateInstance(
                "RealInstalledBoundary",
                new Class<?>[]{OfflineNetworkBoundary.class, ExternalCallLedger.class},
                boundary,
                ledger
        );
        invokePrivate(installed, "close", new Class<?>[0]);
        org.mockito.Mockito.verify(boundary).close();

        OfflineNetworkBoundary abortBoundary = mock(OfflineNetworkBoundary.class);
        Object adapter = newPrivateInstance(
                "LegacyBoundaryAdapter",
                new Class<?>[]{OfflineNetworkBoundary.class, ExternalCallLedger.class},
                abortBoundary,
                ledger
        );
        invokePrivate(adapter, "abortOpen", new Class<?>[0]);
        org.mockito.Mockito.verify(abortBoundary).close();
        assertThatThrownBy(() -> invokePrivate(
                adapter,
                "allowLoopback",
                new Class<?>[]{String.class, int.class},
                "127.0.0.1",
                15432
        )).isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("no longer accepts leases");

        Object sealedAdapter = newPrivateInstance(
                "LegacyBoundaryAdapter",
                new Class<?>[]{OfflineNetworkBoundary.class, ExternalCallLedger.class},
                mock(OfflineNetworkBoundary.class),
                ledger
        );
        java.lang.reflect.Field sealed = sealedAdapter.getClass().getDeclaredField("sealed");
        sealed.setAccessible(true);
        sealed.setBoolean(sealedAdapter, true);
        assertThatThrownBy(() -> invokePrivate(
                sealedAdapter,
                "allowLoopback",
                new Class<?>[]{String.class, int.class},
                "127.0.0.1",
                15432
        )).isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("no longer accepts leases");
    }

    @Test
    void denialProofsFailClosedIfTheyReachSocketOrProcessImplementations()
            throws Throwable {
        Object adapter = newPrivateInstance(
                "LegacyBoundaryAdapter",
                new Class<?>[]{OfflineNetworkBoundary.class, ExternalCallLedger.class},
                mock(OfflineNetworkBoundary.class),
                mock(ExternalCallLedger.class)
        );

        try (MockedConstruction<Socket> sockets = mockConstruction(
                Socket.class,
                (socket, context) -> doThrow(new IOException("unguarded socket"))
                        .when(socket).connect(any(SocketAddress.class))
        )) {
            assertThatThrownBy(() -> invokePrivate(
                    adapter, "proveNetworkDenied", new Class<?>[0]
            )).isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("socket implementation");
        }
        try (MockedConstruction<Socket> sockets = mockConstruction(Socket.class)) {
            assertThatThrownBy(() -> invokePrivate(
                    adapter, "proveNetworkDenied", new Class<?>[0]
            )).isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("unexpectedly connected");
        }

        try (MockedConstruction<ProcessBuilder> builders = mockConstruction(
                ProcessBuilder.class,
                (builder, context) -> when(builder.start())
                        .thenThrow(new IOException("unguarded process"))
        )) {
            assertThatThrownBy(() -> invokePrivate(
                    adapter, "proveProcessDenied", new Class<?>[0]
            )).isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("operating system");
        }
        try (MockedConstruction<ProcessBuilder> builders = mockConstruction(
                ProcessBuilder.class,
                (builder, context) -> when(builder.start()).thenReturn(mock(Process.class))
        )) {
            assertThatThrownBy(() -> invokePrivate(
                    adapter, "proveProcessDenied", new Class<?>[0]
            )).isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("unexpectedly started");
        }
    }

    private Object invokeRealAssemblyInstall() throws Throwable {
        Object assembly = newPrivateInstance(
                "RealLifecycleAssembly", new Class<?>[0]
        );
        return invokePrivate(assembly, "install", new Class<?>[0]);
    }

    private static Object newPrivateInstance(
            String simpleName,
            Class<?>[] parameterTypes,
            Object... arguments
    ) throws Throwable {
        Class<?> type = Class.forName(
                LegacyContainerItLauncherSessionListener.class.getName() + "$" + simpleName
        );
        Constructor<?> constructor = type.getDeclaredConstructor(parameterTypes);
        constructor.setAccessible(true);
        try {
            return constructor.newInstance(arguments);
        } catch (InvocationTargetException failure) {
            throw failure.getCause();
        }
    }

    private static Object invokePrivate(
            Object target,
            String name,
            Class<?>[] parameterTypes,
            Object... arguments
    ) throws Throwable {
        Method method = target.getClass().getDeclaredMethod(name, parameterTypes);
        method.setAccessible(true);
        try {
            return method.invoke(target, arguments);
        } catch (InvocationTargetException failure) {
            throw failure.getCause();
        }
    }

    private LegacyContainerItContract.Activation queueActivation() {
        return LegacyContainerItContract.Activation.forTesting(
                "p007_listener_01234567",
                LegacyContainerItContract.Lane.QUEUE,
                ledgerDirectory,
                null,
                new LegacyContainerEndpoints.RedisEndpoint("127.0.0.1", 16379)
        );
    }
}
