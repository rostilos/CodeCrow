package org.rostilos.codecrow.security.annotations;

import org.springframework.security.access.prepost.PreAuthorize;
import java.lang.annotation.*;

@Target({ElementType.METHOD, ElementType.TYPE})
@Retention(RetentionPolicy.RUNTIME)
@Inherited
@Documented
@PreAuthorize("@workspaceSecurity.isWorkspaceOwner(#workspaceSlug, authentication)")
public @interface IsWorkspaceOwner {
}
