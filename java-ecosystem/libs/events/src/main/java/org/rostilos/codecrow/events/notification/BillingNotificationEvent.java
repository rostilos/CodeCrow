package org.rostilos.codecrow.events.notification;

import org.rostilos.codecrow.events.CodecrowEvent;

import java.math.BigDecimal;

/**
 * Event fired for billing-related notifications.
 */
public class BillingNotificationEvent extends CodecrowEvent {

    public enum BillingEventType {
        PAYMENT_DUE,
        PAYMENT_FAILED,
        PAYMENT_SUCCESS,
        PLAN_CHANGED,
        PLAN_EXPIRED,
        TRIAL_ENDING,
        INVOICE_GENERATED
    }

    private final Long workspaceId;
    private final String workspaceName;
    private final BillingEventType billingEventType;
    private final String planName;
    private final BigDecimal amount;
    private final String currency;
    private final String message;
    private final String invoiceUrl;

    public BillingNotificationEvent(Object source, Long workspaceId, String workspaceName,
                                     BillingEventType billingEventType, String planName,
                                     BigDecimal amount, String currency, String message, String invoiceUrl) {
        super(source);
        this.workspaceId = workspaceId;
        this.workspaceName = workspaceName;
        this.billingEventType = billingEventType;
        this.planName = planName;
        this.amount = amount;
        this.currency = currency;
        this.message = message;
        this.invoiceUrl = invoiceUrl;
    }

    @Override
    public String getEventType() {
        return "BILLING_NOTIFICATION";
    }

    public Long getWorkspaceId() {
        return workspaceId;
    }

    public String getWorkspaceName() {
        return workspaceName;
    }

    public BillingEventType getBillingEventType() {
        return billingEventType;
    }

    public String getPlanName() {
        return planName;
    }

    public BigDecimal getAmount() {
        return amount;
    }

    public String getCurrency() {
        return currency;
    }

    public String getMessage() {
        return message;
    }

    public String getInvoiceUrl() {
        return invoiceUrl;
    }
}
