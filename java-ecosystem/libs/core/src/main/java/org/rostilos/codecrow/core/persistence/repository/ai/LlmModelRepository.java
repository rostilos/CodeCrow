package org.rostilos.codecrow.core.persistence.repository.ai;

import org.rostilos.codecrow.core.model.ai.AIProviderKey;
import org.rostilos.codecrow.core.model.ai.LlmModel;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

@Repository
public interface LlmModelRepository extends JpaRepository<LlmModel, Long> {

    Optional<LlmModel> findByProviderKeyAndModelId(AIProviderKey providerKey, String modelId);

    Page<LlmModel> findByProviderKey(AIProviderKey providerKey, Pageable pageable);

    @Query("SELECT m FROM LlmModel m WHERE m.providerKey = :provider AND " +
           "(LOWER(m.modelId) LIKE LOWER(CONCAT('%', :search, '%')) OR " +
           "LOWER(m.displayName) LIKE LOWER(CONCAT('%', :search, '%')))")
    Page<LlmModel> searchByProviderAndQuery(
            @Param("provider") AIProviderKey provider,
            @Param("search") String search,
            Pageable pageable
    );

    @Query("SELECT m FROM LlmModel m WHERE " +
           "LOWER(m.modelId) LIKE LOWER(CONCAT('%', :search, '%')) OR " +
           "LOWER(m.displayName) LIKE LOWER(CONCAT('%', :search, '%'))")
    Page<LlmModel> searchByQuery(@Param("search") String search, Pageable pageable);

    List<LlmModel> findByProviderKeyOrderByModelIdAsc(AIProviderKey providerKey);

    long countByProviderKey(AIProviderKey providerKey);

    @Modifying
    @Query("DELETE FROM LlmModel m WHERE m.providerKey = :provider AND m.lastSyncedAt < :threshold")
    int deleteStaleModels(@Param("provider") AIProviderKey provider, @Param("threshold") OffsetDateTime threshold);

    boolean existsByProviderKey(AIProviderKey providerKey);
}
