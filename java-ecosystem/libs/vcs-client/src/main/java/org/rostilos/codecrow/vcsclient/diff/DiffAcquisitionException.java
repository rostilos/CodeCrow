package org.rostilos.codecrow.vcsclient.diff;

import java.io.IOException;
import java.util.Objects;

/**
 * Checked acquisition failure that preserves the non-clean inventory reason.
 */
public class DiffAcquisitionException extends IOException {

    private final ExactDiffInventory.GapType reason;

    public DiffAcquisitionException(ExactDiffInventory.GapType reason, String message) {
        super(message);
        this.reason = Objects.requireNonNull(reason, "reason");
    }

    public DiffAcquisitionException(
            ExactDiffInventory.GapType reason,
            String message,
            Throwable cause
    ) {
        super(message, cause);
        this.reason = Objects.requireNonNull(reason, "reason");
    }

    public ExactDiffInventory.GapType reason() {
        return reason;
    }
}
