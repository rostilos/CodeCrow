package org.rostilos.codecrow.testsupport.legacy;

import java.util.List;
import java.util.Objects;
import java.util.function.Predicate;

/** Proves the guarded property contract is executing in exactly its target test module. */
final class LegacyContainerModuleVisibility {

    private LegacyContainerModuleVisibility() {
    }

    static void assertExact(LegacyContainerItContract.Activation activation) {
        assertExact(activation, LegacyContainerModuleVisibility::isVisible);
    }

    static void assertExact(
            LegacyContainerItContract.Activation activation,
            Predicate<String> visibility
    ) {
        Objects.requireNonNull(activation, "activation");
        Objects.requireNonNull(visibility, "visibility");
        List<String> targetSelectors = selectors(activation.lane());
        for (String selector : targetSelectors) {
            if (!visibility.test(selector)) {
                throw new IllegalStateException(
                        "guarded target selector is not visible in the current test module: "
                                + selector
                );
            }
        }
        for (LegacyContainerItContract.Lane lane : LegacyContainerItContract.Lane.values()) {
            if (lane == activation.lane()) {
                continue;
            }
            for (String selector : selectors(lane)) {
                if (visibility.test(selector)) {
                    throw new IllegalStateException(
                            "guarded non-target selector is visible in the current test module: "
                                    + selector
                    );
                }
            }
        }
    }

    private static List<String> selectors(LegacyContainerItContract.Lane lane) {
        return List.of(lane.selectors().split(",", -1));
    }

    private static boolean isVisible(String className) {
        ClassLoader contextLoader = Thread.currentThread().getContextClassLoader();
        ClassLoader loader = contextLoader == null
                ? LegacyContainerModuleVisibility.class.getClassLoader()
                : contextLoader;
        try {
            Class.forName(className, false, loader);
            return true;
        } catch (ClassNotFoundException missing) {
            return false;
        } catch (LinkageError brokenTarget) {
            throw new IllegalStateException(
                    "guarded selector cannot be linked in the current test module: " + className,
                    brokenTarget
            );
        }
    }
}
