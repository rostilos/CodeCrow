package org.rostilos.codecrow.security.annotations;

import org.springframework.security.access.prepost.PreAuthorize;
import java.lang.annotation.*;

/**
 * Restricts access to users with {@code ROLE_ADMIN} authority,
 * which maps from {@code EAccountType.TYPE_ADMIN}.
 * Used on Site Admin endpoints (instance-wide settings).
 */
@Target({ElementType.METHOD, ElementType.TYPE})
@Retention(RetentionPolicy.RUNTIME)
@Inherited
@Documented
@PreAuthorize("hasRole('ROLE_ADMIN')")
public @interface IsSiteAdmin {
}
