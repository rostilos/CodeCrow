package org.rostilos.codecrow.analysisengine.delivery;

import java.io.IOException;
import java.util.Objects;
import java.util.regex.Pattern;

/** Typed provider failure whose disposition is known before acknowledgement. */
public final class ReviewDeliveryFailure extends IOException {
    private static final long serialVersionUID = 1L;
    private static final Pattern REASON = Pattern.compile("[a-z0-9_]{1,64}");

    private final ReviewDeliveryFailureDisposition disposition;
    private final String reasonCode;

    public ReviewDeliveryFailure(
            ReviewDeliveryFailureDisposition disposition,
            String reasonCode) {
        super(requireReason(reasonCode));
        this.disposition = Objects.requireNonNull(
                disposition, "disposition");
        this.reasonCode = reasonCode;
    }

    public ReviewDeliveryFailureDisposition disposition() {
        return disposition;
    }

    public String reasonCode() {
        return reasonCode;
    }

    private static String requireReason(String value) {
        if (value == null || !REASON.matcher(value).matches()) {
            throw new IllegalArgumentException("reasonCode is invalid");
        }
        return value;
    }
}
