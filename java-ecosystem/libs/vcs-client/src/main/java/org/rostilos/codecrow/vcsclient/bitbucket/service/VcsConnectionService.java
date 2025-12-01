package org.rostilos.codecrow.vcsclient.bitbucket.service;

import okhttp3.OkHttpClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.springframework.stereotype.Service;

/**
 * @deprecated Use {@link VcsClientProvider} instead.
 * This class is kept for backward compatibility but delegates to VcsClientProvider.
 */
@Deprecated(since = "2.0", forRemoval = true)
@Service
public class VcsConnectionService {
    
    private final VcsClientProvider vcsClientProvider;

    public VcsConnectionService(VcsClientProvider vcsClientProvider) {
        this.vcsClientProvider = vcsClientProvider;
    }

    /**
     * @deprecated Use {@link VcsClientProvider#getHttpClient(Long, Long)} instead.
     */
    @Deprecated(since = "2.0", forRemoval = true)
    public OkHttpClient getBitbucketAuthorizedClient(Long workspaceId, Long connectionId) {
        return vcsClientProvider.getHttpClient(workspaceId, connectionId);
    }
}