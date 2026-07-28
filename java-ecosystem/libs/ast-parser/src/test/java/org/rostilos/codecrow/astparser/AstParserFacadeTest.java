package org.rostilos.codecrow.astparser;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.astparser.api.AstParseException;
import org.rostilos.codecrow.astparser.model.SymbolInfo;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class AstParserFacadeTest {

    @Test
    void host_starts_and_abstains_when_no_language_plugins_are_installed() {
        try (AstParserFacade facade = AstParserFacade.createDefault()) {
            assertThat(facade.isSupported("Example.java")).isFalse();
            assertThat(facade.resolveAllScopes("Example.java", "class Example {}")).isEmpty();
            assertThat(facade.extractSymbols("Example.java", "class Example {}"))
                    .isEqualTo(SymbolInfo.empty());
            assertThatThrownBy(() -> facade.parse("Example.java", "class Example {}"))
                    .isInstanceOf(AstParseException.class)
                    .hasMessageContaining("Unsupported file type");
        }
    }
}
