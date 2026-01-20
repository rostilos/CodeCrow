package org.rostilos.codecrow.core.utils;

import jakarta.validation.ConstraintValidatorContext;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.Mock;
import org.mockito.MockitoAnnotations;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.when;

class EnumNamePatternValidatorTest {

    private EnumNamePatternValidator validator;

    @Mock
    private EnumNamePattern annotation;

    @Mock
    private ConstraintValidatorContext context;

    @BeforeEach
    void setUp() {
        MockitoAnnotations.openMocks(this);
        validator = new EnumNamePatternValidator();
    }

    @Test
    void testIsValid_WithNullValue() {
        when(annotation.regexp()).thenReturn("[A-Z_]+");
        validator.initialize(annotation);
        
        assertThat(validator.isValid(null, context)).isTrue();
    }

    @Test
    void testIsValid_WithMatchingValue() {
        when(annotation.regexp()).thenReturn("[A-Z_]+");
        validator.initialize(annotation);
        
        assertThat(validator.isValid("VALID_VALUE", context)).isTrue();
        assertThat(validator.isValid("ANOTHER_VALID", context)).isTrue();
    }

    @Test
    void testIsValid_WithNonMatchingValue() {
        when(annotation.regexp()).thenReturn("[A-Z_]+");
        validator.initialize(annotation);
        
        assertThat(validator.isValid("invalidValue", context)).isFalse();
        assertThat(validator.isValid("123", context)).isFalse();
        assertThat(validator.isValid("Invalid-Value", context)).isFalse();
    }

    @Test
    void testInitialize_WithInvalidRegex() {
        when(annotation.regexp()).thenReturn("[invalid(");
        
        assertThatThrownBy(() -> validator.initialize(annotation))
            .isInstanceOf(IllegalArgumentException.class)
            .hasMessageContaining("Invalid regex for EnumNamePattern");
    }

    @Test
    void testIsValid_WithDigitsPattern() {
        when(annotation.regexp()).thenReturn("\\d+");
        validator.initialize(annotation);
        
        assertThat(validator.isValid("123", context)).isTrue();
        assertThat(validator.isValid("456789", context)).isTrue();
        assertThat(validator.isValid("abc", context)).isFalse();
    }

    @Test
    void testIsValid_WithComplexPattern() {
        when(annotation.regexp()).thenReturn("[A-Z][A-Z0-9_]*");
        validator.initialize(annotation);
        
        assertThat(validator.isValid("VALID_123", context)).isTrue();
        assertThat(validator.isValid("V", context)).isTrue();
        assertThat(validator.isValid("123INVALID", context)).isFalse();
        assertThat(validator.isValid("", context)).isFalse();
    }

    @Test
    void testIsValid_WithEmptyString() {
        when(annotation.regexp()).thenReturn("[A-Z]+");
        validator.initialize(annotation);
        
        assertThat(validator.isValid("", context)).isFalse();
    }

    @Test
    void testIsValid_WithEmailPattern() {
        when(annotation.regexp()).thenReturn("[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}");
        validator.initialize(annotation);
        
        assertThat(validator.isValid("test@example.com", context)).isTrue();
        assertThat(validator.isValid("user.name+tag@domain.co.uk", context)).isTrue();
        assertThat(validator.isValid("invalid-email", context)).isFalse();
    }
}
