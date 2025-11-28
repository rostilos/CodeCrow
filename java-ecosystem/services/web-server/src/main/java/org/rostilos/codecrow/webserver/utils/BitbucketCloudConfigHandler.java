package org.rostilos.codecrow.webserver.utils;

import org.rostilos.codecrow.core.model.vcs.config.cloud.BitbucketCloudConfig;
import org.rostilos.codecrow.webserver.dto.request.vcs.bitbucket.cloud.BitbucketCloudCreateRequest;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.springframework.stereotype.Component;

import java.security.GeneralSecurityException;

@Component
public class BitbucketCloudConfigHandler {
    private final TokenEncryptionService tokenEncryptionService;

    public BitbucketCloudConfigHandler(TokenEncryptionService tokenEncryptionService) {
        this.tokenEncryptionService = tokenEncryptionService;
    }

    public BitbucketCloudConfig buildBitbucketConfigFromRequest(BitbucketCloudCreateRequest request) throws GeneralSecurityException {
        return new BitbucketCloudConfig(
                tokenEncryptionService.encrypt(request.getOAuthKey()),
                tokenEncryptionService.encrypt(request.getOAuthSecret()),
                request.getWorkspaceId()
        );
    }

    public BitbucketCloudConfig updateBitbucketConfigFromRequest(BitbucketCloudConfig oldConfig, BitbucketCloudCreateRequest request) throws GeneralSecurityException {
        return new BitbucketCloudConfig(
                request.getOAuthKey() != null && !request.getOAuthKey().isBlank()
                        ? tokenEncryptionService.encrypt(request.getOAuthKey())
                        : oldConfig.oAuthKey(),
                request.getOAuthSecret() != null && !request.getOAuthSecret().isBlank()
                        ? tokenEncryptionService.encrypt(request.getOAuthSecret())
                        : oldConfig.oAuthToken(),
                request.getWorkspaceId() != null ? request.getWorkspaceId() : oldConfig.workspaceId()
        );
    }
}
