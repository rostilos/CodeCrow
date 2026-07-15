package org.rostilos.codecrow.testsupport.offline;

/** Raised when a fake receives more calls than its registered schedule. */
public final class ScenarioExhaustedException extends IllegalStateException {

    public ScenarioExhaustedException(String message) {
        super(message);
    }
}
