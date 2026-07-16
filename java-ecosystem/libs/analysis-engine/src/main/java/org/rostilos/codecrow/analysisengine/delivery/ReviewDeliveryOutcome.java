package org.rostilos.codecrow.analysisengine.delivery;

import java.util.Objects;
import java.util.regex.Pattern;

/** Durable result of one delivery attempt or terminal stale decision. */
public record ReviewDeliveryOutcome(
        ReviewDeliveryState state,
        String intentId,
        String idempotencyKey,
        int attemptCount,
        String reasonCode,
        String providerReceiptId) {
    private static final Pattern IDENTIFIER =
            Pattern.compile("[A-Za-z0-9][A-Za-z0-9._:-]{0,159}");
    private static final Pattern SHA_256 = Pattern.compile("[0-9a-f]{64}");
    private static final Pattern REASON = Pattern.compile("[a-z0-9_]{1,64}");

    public ReviewDeliveryOutcome {
        state = Objects.requireNonNull(state, "state");
        requireMatch(intentId, IDENTIFIER, "intentId");
        requireMatch(idempotencyKey, SHA_256, "idempotencyKey");
        if (attemptCount < 0
                || (state == ReviewDeliveryState.PENDING && attemptCount != 0)
                || (state != ReviewDeliveryState.PENDING && attemptCount == 0)) {
            throw new IllegalArgumentException(
                    "attemptCount conflicts with delivery state");
        }
        switch (state) {
            case PENDING, IN_FLIGHT -> {
                if (reasonCode != null || providerReceiptId != null) {
                    throw new IllegalArgumentException(
                            "non-terminal delivery state cannot carry an outcome");
                }
            }
            case RETRYABLE_FAILED, PERMANENT_FAILED, AMBIGUOUS -> {
                requireMatch(reasonCode, REASON, "reasonCode");
                if (providerReceiptId != null) {
                    throw new IllegalArgumentException(
                            "failed or ambiguous delivery cannot carry a provider receipt");
                }
            }
            case DELIVERED -> {
                if (reasonCode != null) {
                    throw new IllegalArgumentException(
                            "delivered outcome cannot carry a failure reason");
                }
                requireText(providerReceiptId, "providerReceiptId");
            }
            case STALE -> {
                if (!"stale_head".equals(reasonCode)
                        || providerReceiptId != null) {
                    throw new IllegalArgumentException(
                            "stale outcome must use the stale_head reason");
                }
            }
            default -> throw new IllegalStateException(
                    "unsupported delivery state " + state);
        }
    }

    private static void requireMatch(
            String value,
            Pattern pattern,
            String field) {
        if (value == null || !pattern.matcher(value).matches()) {
            throw new IllegalArgumentException(field + " is invalid");
        }
    }

    private static void requireText(String value, String field) {
        if (value == null
                || value.isBlank()
                || value.length() > 4096
                || value.indexOf('\0') >= 0) {
            throw new IllegalArgumentException(field + " is invalid");
        }
    }
}
