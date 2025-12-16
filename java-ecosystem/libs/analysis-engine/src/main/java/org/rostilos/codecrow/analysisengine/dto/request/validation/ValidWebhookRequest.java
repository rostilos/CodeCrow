package org.rostilos.codecrow.analysisengine.dto.request.validation;

import jakarta.validation.Constraint;
import jakarta.validation.Payload;
import java.lang.annotation.*;

@Target({ElementType.TYPE})
@Retention(RetentionPolicy.RUNTIME)
@Constraint(validatedBy = WebhookRequestValidator.class)
@Documented
public @interface ValidWebhookRequest {
    String message() default "Invalid webhook request: PR_REVIEW requires pullRequestId";
    Class<?>[] groups() default {};
    Class<? extends Payload>[] payload() default {};
}
