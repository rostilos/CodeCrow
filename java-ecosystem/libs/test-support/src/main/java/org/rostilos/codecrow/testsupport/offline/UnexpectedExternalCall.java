package org.rostilos.codecrow.testsupport.offline;

import java.util.Objects;

/** Raised before an unregistered network or process boundary can execute. */
public final class UnexpectedExternalCall extends SecurityException {

    private final ExternalCall call;

    public UnexpectedExternalCall(ExternalCall call) {
        super(
                "unregistered outbound target: "
                        + Objects.requireNonNull(call, "call").target()
        );
        this.call = call;
    }

    /** The exact object appended to the originating ledger for identity-safe acknowledgement. */
    public ExternalCall call() {
        return call;
    }
}
