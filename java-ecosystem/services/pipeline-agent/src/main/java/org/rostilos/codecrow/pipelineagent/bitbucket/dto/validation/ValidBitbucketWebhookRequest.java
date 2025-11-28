package org.rostilos.codecrow.pipelineagent.bitbucket.dto.validation;

import jakarta.validation.Constraint;
import jakarta.validation.Payload;
import java.lang.annotation.*;

@Target({ElementType.TYPE})
@Retention(RetentionPolicy.RUNTIME)
@Constraint(validatedBy = BitbucketWebhookRequestValidator.class)
@Documented
public @interface ValidBitbucketWebhookRequest {
    String message() default "Invalid webhook request: PR_REVIEW requires pullRequestId";
    Class<?>[] groups() default {};
    Class<? extends Payload>[] payload() default {};
}

