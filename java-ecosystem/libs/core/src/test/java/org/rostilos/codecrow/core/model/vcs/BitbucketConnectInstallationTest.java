package org.rostilos.codecrow.core.model.vcs;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.workspace.Workspace;

import java.time.LocalDateTime;

import static org.assertj.core.api.Assertions.assertThat;

class BitbucketConnectInstallationTest {

    @Test
    void testDefaultConstructor() {
        BitbucketConnectInstallation installation = new BitbucketConnectInstallation();
        assertThat(installation.getId()).isNull();
        assertThat(installation.getClientKey()).isNull();
        assertThat(installation.getSharedSecret()).isNull();
        assertThat(installation.getBitbucketWorkspaceUuid()).isNull();
        assertThat(installation.getBitbucketWorkspaceSlug()).isNull();
        assertThat(installation.getBitbucketWorkspaceName()).isNull();
        assertThat(installation.getInstalledByUuid()).isNull();
        assertThat(installation.getInstalledByUsername()).isNull();
        assertThat(installation.getBaseApiUrl()).isNull();
        assertThat(installation.getCodecrowWorkspace()).isNull();
        assertThat(installation.getVcsConnection()).isNull();
        assertThat(installation.isEnabled()).isTrue();
        assertThat(installation.getInstalledAt()).isNotNull(); // initialized in constructor
        assertThat(installation.getUpdatedAt()).isNull();
    }

    @Test
    void testSetAndGetClientKey() {
        BitbucketConnectInstallation installation = new BitbucketConnectInstallation();
        installation.setClientKey("client-key-123");
        assertThat(installation.getClientKey()).isEqualTo("client-key-123");
    }

    @Test
    void testSetAndGetSharedSecret() {
        BitbucketConnectInstallation installation = new BitbucketConnectInstallation();
        installation.setSharedSecret("shared-secret-456");
        assertThat(installation.getSharedSecret()).isEqualTo("shared-secret-456");
    }

    @Test
    void testSetAndGetBitbucketWorkspaceUuid() {
        BitbucketConnectInstallation installation = new BitbucketConnectInstallation();
        installation.setBitbucketWorkspaceUuid("{workspace-uuid}");
        assertThat(installation.getBitbucketWorkspaceUuid()).isEqualTo("{workspace-uuid}");
    }

    @Test
    void testSetAndGetBitbucketWorkspaceSlug() {
        BitbucketConnectInstallation installation = new BitbucketConnectInstallation();
        installation.setBitbucketWorkspaceSlug("my-workspace");
        assertThat(installation.getBitbucketWorkspaceSlug()).isEqualTo("my-workspace");
    }

    @Test
    void testSetAndGetBitbucketWorkspaceName() {
        BitbucketConnectInstallation installation = new BitbucketConnectInstallation();
        installation.setBitbucketWorkspaceName("My Workspace");
        assertThat(installation.getBitbucketWorkspaceName()).isEqualTo("My Workspace");
    }

    @Test
    void testSetAndGetInstalledByUuid() {
        BitbucketConnectInstallation installation = new BitbucketConnectInstallation();
        installation.setInstalledByUuid("{user-uuid}");
        assertThat(installation.getInstalledByUuid()).isEqualTo("{user-uuid}");
    }

    @Test
    void testSetAndGetInstalledByUsername() {
        BitbucketConnectInstallation installation = new BitbucketConnectInstallation();
        installation.setInstalledByUsername("john.doe");
        assertThat(installation.getInstalledByUsername()).isEqualTo("john.doe");
    }

    @Test
    void testSetAndGetBaseApiUrl() {
        BitbucketConnectInstallation installation = new BitbucketConnectInstallation();
        installation.setBaseApiUrl("https://api.bitbucket.org/2.0");
        assertThat(installation.getBaseApiUrl()).isEqualTo("https://api.bitbucket.org/2.0");
    }

    @Test
    void testSetAndGetCodecrowWorkspace() {
        BitbucketConnectInstallation installation = new BitbucketConnectInstallation();
        Workspace workspace = new Workspace();
        installation.setCodecrowWorkspace(workspace);
        assertThat(installation.getCodecrowWorkspace()).isEqualTo(workspace);
    }

    @Test
    void testSetAndGetVcsConnection() {
        BitbucketConnectInstallation installation = new BitbucketConnectInstallation();
        VcsConnection vcsConnection = new VcsConnection();
        installation.setVcsConnection(vcsConnection);
        assertThat(installation.getVcsConnection()).isEqualTo(vcsConnection);
    }

    @Test
    void testSetAndGetEnabled() {
        BitbucketConnectInstallation installation = new BitbucketConnectInstallation();
        installation.setEnabled(false);
        assertThat(installation.isEnabled()).isFalse();
        installation.setEnabled(true);
        assertThat(installation.isEnabled()).isTrue();
    }

    @Test
    void testSetAndGetInstalledAt() {
        BitbucketConnectInstallation installation = new BitbucketConnectInstallation();
        LocalDateTime installedAt = LocalDateTime.now();
        installation.setInstalledAt(installedAt);
        assertThat(installation.getInstalledAt()).isEqualTo(installedAt);
    }

    @Test
    void testSetAndGetUpdatedAt() {
        BitbucketConnectInstallation installation = new BitbucketConnectInstallation();
        LocalDateTime updatedAt = LocalDateTime.now();
        installation.setUpdatedAt(updatedAt);
        assertThat(installation.getUpdatedAt()).isEqualTo(updatedAt);
    }

    @Test
    void testFullInstallationSetup() {
        BitbucketConnectInstallation installation = new BitbucketConnectInstallation();
        LocalDateTime now = LocalDateTime.now();
        Workspace workspace = new Workspace();
        VcsConnection vcsConnection = new VcsConnection();
        
        installation.setClientKey("client-123");
        installation.setSharedSecret("secret-456");
        installation.setBitbucketWorkspaceUuid("{uuid}");
        installation.setBitbucketWorkspaceSlug("workspace-slug");
        installation.setBitbucketWorkspaceName("Workspace Name");
        installation.setInstalledByUuid("{user-uuid}");
        installation.setInstalledByUsername("user");
        installation.setBaseApiUrl("https://api.bitbucket.org/2.0");
        installation.setCodecrowWorkspace(workspace);
        installation.setVcsConnection(vcsConnection);
        installation.setEnabled(true);
        installation.setInstalledAt(now);
        installation.setUpdatedAt(now);
        
        assertThat(installation.getClientKey()).isEqualTo("client-123");
        assertThat(installation.getSharedSecret()).isEqualTo("secret-456");
        assertThat(installation.getBitbucketWorkspaceUuid()).isEqualTo("{uuid}");
        assertThat(installation.getBitbucketWorkspaceSlug()).isEqualTo("workspace-slug");
        assertThat(installation.getBitbucketWorkspaceName()).isEqualTo("Workspace Name");
        assertThat(installation.getInstalledByUuid()).isEqualTo("{user-uuid}");
        assertThat(installation.getInstalledByUsername()).isEqualTo("user");
        assertThat(installation.getBaseApiUrl()).isEqualTo("https://api.bitbucket.org/2.0");
        assertThat(installation.getCodecrowWorkspace()).isEqualTo(workspace);
        assertThat(installation.getVcsConnection()).isEqualTo(vcsConnection);
        assertThat(installation.isEnabled()).isTrue();
        assertThat(installation.getInstalledAt()).isEqualTo(now);
        assertThat(installation.getUpdatedAt()).isEqualTo(now);
    }

    @Test
    void testEnabledDefaultsToTrue() {
        BitbucketConnectInstallation installation = new BitbucketConnectInstallation();
        assertThat(installation.isEnabled()).isTrue();
    }
}
