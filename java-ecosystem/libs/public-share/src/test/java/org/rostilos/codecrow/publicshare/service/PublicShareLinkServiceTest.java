package org.rostilos.codecrow.publicshare.service;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.publicshare.api.IssuedPublicShare;
import org.rostilos.codecrow.publicshare.api.ResolvedPublicShare;
import org.rostilos.codecrow.publicshare.model.PublicShareLink;
import org.rostilos.codecrow.publicshare.persistence.PublicShareLinkRepository;

import java.security.SecureRandom;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class PublicShareLinkServiceTest {

    @Mock
    private PublicShareLinkRepository repository;
    @Mock
    private SecureRandom secureRandom;

    @Test
    void storesOnlyAHashAndResolvesTheOpaqueResourceReference() {
        when(repository.save(any(PublicShareLink.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));
        PublicShareLinkService service = new PublicShareLinkService(repository, secureRandom);

        IssuedPublicShare issued = service.issue("report-preview", "internal-key-42");

        ArgumentCaptor<PublicShareLink> captor = ArgumentCaptor.forClass(PublicShareLink.class);
        verify(repository).save(captor.capture());
        PublicShareLink stored = captor.getValue();
        assertThat(issued.token()).startsWith("ccs_");
        assertThat(stored.getTokenHash())
                .hasSize(64)
                .isNotEqualTo(issued.token())
                .isEqualTo(PublicShareLinkService.hash(issued.token()));

        when(repository.findByTokenHash(stored.getTokenHash())).thenReturn(Optional.of(stored));
        assertThat(service.resolve(issued.token()))
                .contains(new ResolvedPublicShare("report-preview", "internal-key-42"));
    }

    @Test
    void rejectsValuesThatAreNotPublicShareTokensWithoutQueryingByRawValue() {
        PublicShareLinkService service = new PublicShareLinkService(repository, secureRandom);

        assertThat(service.resolve("eyJhbGciOiJIUzI1NiJ9.jwt.payload")).isEmpty();
        assertThat(service.resolve("ccs_short")).isEmpty();
    }
}
