package org.rostilos.codecrow.vcsclient.bitbucket.service;

import okhttp3.OkHttpClient;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.config.cloud.BitbucketCloudConfig;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsConnectionRepository;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.vcsclient.HttpAuthorizedClientFactory;
import org.springframework.stereotype.Service;
import org.springframework.web.context.annotation.RequestScope;

import java.security.GeneralSecurityException;
import java.util.HashMap;
import java.util.Map;

@Service
//@RequestScope
public class VcsConnectionService {
    private final VcsConnectionRepository vcsConnectionRepository;
    private final HttpAuthorizedClientFactory bitbucketHttpAuthorizedClientFactory;
    private final TokenEncryptionService tokenEncryptionService;
    private final Map<Long, OkHttpClient> bitbucketClientCache = new HashMap<>();

    public VcsConnectionService(
            VcsConnectionRepository vcsConnectionRepository,
            HttpAuthorizedClientFactory bitbucketHttpAuthorizedClientFactory,
            TokenEncryptionService tokenEncryptionService
    ) {
        this.vcsConnectionRepository = vcsConnectionRepository;
        this.bitbucketHttpAuthorizedClientFactory = bitbucketHttpAuthorizedClientFactory;
        this.tokenEncryptionService = tokenEncryptionService;
    }

    public OkHttpClient getBitbucketAuthorizedClient(Long workspaceId, Long connectionId) {
        //TODO: cache per request
        //return bitbucketClientCache.computeIfAbsent(connectionId, id -> {
            try {
                VcsConnection connection = vcsConnectionRepository.findByWorkspace_IdAndId(workspaceId, connectionId)
                        .orElseThrow(() -> new IllegalArgumentException("Workspace not found"));

                BitbucketCloudConfig bitbucketCfg = (BitbucketCloudConfig) connection.getConfiguration();

                String oAuthKey = decrypt(bitbucketCfg.oAuthKey());
                String oAuthSecret = decrypt(bitbucketCfg.oAuthToken());

                return bitbucketHttpAuthorizedClientFactory.createClient(oAuthKey, oAuthSecret, EVcsProvider.BITBUCKET_CLOUD.toString());
            } catch (GeneralSecurityException e) {
                throw new RuntimeException("Failed to create Bitbucket client", e);
            }
        //});
    }

    public String decrypt(String encrypted) throws GeneralSecurityException {
        if (encrypted == null) return null;
        return tokenEncryptionService.decrypt(encrypted);
    }
}