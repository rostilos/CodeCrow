package org.rostilos.codecrow.core.model.project.config;

/**
 * The type of a custom project rule.
 * <ul>
 *   <li><b>ENFORCE</b> – The reviewer MUST flag violations of this rule.</li>
 *   <li><b>SUPPRESS</b> – The reviewer MUST NOT flag issues described by this rule
 *       (useful for silencing known false-positive patterns).</li>
 * </ul>
 */
public enum RuleType {
    ENFORCE,
    SUPPRESS
}
