package org.rostilos.codecrow.integration.config;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Configuration;

/**
 * Configuration properties for test credentials.
 * These are loaded from test-credentials.yml or environment variables.
 */
@Configuration
@EnableConfigurationProperties
@ConfigurationProperties(prefix = "test-credentials")
public class TestCredentialsConfig {

    private BitbucketCredentials bitbucket = new BitbucketCredentials();
    private GitLabCredentials gitlab = new GitLabCredentials();
    private GitHubCredentials github = new GitHubCredentials();
    private AICredentials ai = new AICredentials();
    private RagCredentials rag = new RagCredentials();
    private TestUsersCredentials testUsers = new TestUsersCredentials();

    public BitbucketCredentials getBitbucket() {
        return bitbucket;
    }

    public void setBitbucket(BitbucketCredentials bitbucket) {
        this.bitbucket = bitbucket;
    }

    public GitLabCredentials getGitlab() {
        return gitlab;
    }

    public void setGitlab(GitLabCredentials gitlab) {
        this.gitlab = gitlab;
    }

    public GitHubCredentials getGithub() {
        return github;
    }

    public void setGithub(GitHubCredentials github) {
        this.github = github;
    }

    public AICredentials getAi() {
        return ai;
    }

    public void setAi(AICredentials ai) {
        this.ai = ai;
    }

    public RagCredentials getRag() {
        return rag;
    }

    public void setRag(RagCredentials rag) {
        this.rag = rag;
    }

    public TestUsersCredentials getTestUsers() {
        return testUsers;
    }

    public void setTestUsers(TestUsersCredentials testUsers) {
        this.testUsers = testUsers;
    }

    public static class BitbucketCredentials {
        private CloudCredentials cloud = new CloudCredentials();
        private ServerCredentials server = new ServerCredentials();

        public CloudCredentials getCloud() {
            return cloud;
        }

        public void setCloud(CloudCredentials cloud) {
            this.cloud = cloud;
        }

        public ServerCredentials getServer() {
            return server;
        }

        public void setServer(ServerCredentials server) {
            this.server = server;
        }

        public static class CloudCredentials {
            private OAuthCredentials oauth = new OAuthCredentials();
            private AppCredentials app = new AppCredentials();
            private String testWorkspace;
            private String testRepo;

            public OAuthCredentials getOauth() {
                return oauth;
            }

            public void setOauth(OAuthCredentials oauth) {
                this.oauth = oauth;
            }

            public AppCredentials getApp() {
                return app;
            }

            public void setApp(AppCredentials app) {
                this.app = app;
            }

            public String getTestWorkspace() {
                return testWorkspace;
            }

            public void setTestWorkspace(String testWorkspace) {
                this.testWorkspace = testWorkspace;
            }

            public String getTestRepo() {
                return testRepo;
            }

            public void setTestRepo(String testRepo) {
                this.testRepo = testRepo;
            }
        }

        public static class ServerCredentials {
            private String url;
            private String username;
            private String password;
            private String personalToken;

            public String getUrl() {
                return url;
            }

            public void setUrl(String url) {
                this.url = url;
            }

            public String getUsername() {
                return username;
            }

            public void setUsername(String username) {
                this.username = username;
            }

            public String getPassword() {
                return password;
            }

            public void setPassword(String password) {
                this.password = password;
            }

            public String getPersonalToken() {
                return personalToken;
            }

            public void setPersonalToken(String personalToken) {
                this.personalToken = personalToken;
            }
        }
    }

    public static class OAuthCredentials {
        private String clientId;
        private String clientSecret;
        private String accessToken;
        private String refreshToken;

        public String getClientId() {
            return clientId;
        }

        public void setClientId(String clientId) {
            this.clientId = clientId;
        }

        public String getClientSecret() {
            return clientSecret;
        }

        public void setClientSecret(String clientSecret) {
            this.clientSecret = clientSecret;
        }

        public String getAccessToken() {
            return accessToken;
        }

        public void setAccessToken(String accessToken) {
            this.accessToken = accessToken;
        }

        public String getRefreshToken() {
            return refreshToken;
        }

        public void setRefreshToken(String refreshToken) {
            this.refreshToken = refreshToken;
        }
    }

    public static class AppCredentials {
        private String key;
        private String secret;
        private String clientId;
        private String clientSecret;

        public String getKey() {
            return key;
        }

        public void setKey(String key) {
            this.key = key;
        }

        public String getSecret() {
            return secret;
        }

        public void setSecret(String secret) {
            this.secret = secret;
        }

        public String getClientId() {
            return clientId;
        }

        public void setClientId(String clientId) {
            this.clientId = clientId;
        }

        public String getClientSecret() {
            return clientSecret;
        }

        public void setClientSecret(String clientSecret) {
            this.clientSecret = clientSecret;
        }
    }

    public static class GitLabCredentials {
        private OAuthCredentials oauth = new OAuthCredentials();
        private String personalToken;
        private String testGroup;
        private String testProject;

        public OAuthCredentials getOauth() {
            return oauth;
        }

        public void setOauth(OAuthCredentials oauth) {
            this.oauth = oauth;
        }

        public String getPersonalToken() {
            return personalToken;
        }

        public void setPersonalToken(String personalToken) {
            this.personalToken = personalToken;
        }

        public String getTestGroup() {
            return testGroup;
        }

        public void setTestGroup(String testGroup) {
            this.testGroup = testGroup;
        }

        public String getTestProject() {
            return testProject;
        }

        public void setTestProject(String testProject) {
            this.testProject = testProject;
        }
    }

    public static class GitHubCredentials {
        private OAuthCredentials oauth = new OAuthCredentials();
        private AppCredentials app = new AppCredentials();
        private String personalToken;
        private String testOwner;
        private String testRepo;
        private String installationId;

        public OAuthCredentials getOauth() {
            return oauth;
        }

        public void setOauth(OAuthCredentials oauth) {
            this.oauth = oauth;
        }

        public AppCredentials getApp() {
            return app;
        }

        public void setApp(AppCredentials app) {
            this.app = app;
        }

        public String getPersonalToken() {
            return personalToken;
        }

        public void setPersonalToken(String personalToken) {
            this.personalToken = personalToken;
        }

        public String getTestOwner() {
            return testOwner;
        }

        public void setTestOwner(String testOwner) {
            this.testOwner = testOwner;
        }

        public String getTestRepo() {
            return testRepo;
        }

        public void setTestRepo(String testRepo) {
            this.testRepo = testRepo;
        }

        public String getInstallationId() {
            return installationId;
        }

        public void setInstallationId(String installationId) {
            this.installationId = installationId;
        }
    }

    public static class AICredentials {
        private AIProviderCredentials openai = new AIProviderCredentials();
        private AIProviderCredentials anthropic = new AIProviderCredentials();
        private AIProviderCredentials openrouter = new AIProviderCredentials();

        public AIProviderCredentials getOpenai() {
            return openai;
        }

        public void setOpenai(AIProviderCredentials openai) {
            this.openai = openai;
        }

        public AIProviderCredentials getAnthropic() {
            return anthropic;
        }

        public void setAnthropic(AIProviderCredentials anthropic) {
            this.anthropic = anthropic;
        }

        public AIProviderCredentials getOpenrouter() {
            return openrouter;
        }

        public void setOpenrouter(AIProviderCredentials openrouter) {
            this.openrouter = openrouter;
        }
    }

    public static class AIProviderCredentials {
        private String apiKey;
        private String model;

        public String getApiKey() {
            return apiKey;
        }

        public void setApiKey(String apiKey) {
            this.apiKey = apiKey;
        }

        public String getModel() {
            return model;
        }

        public void setModel(String model) {
            this.model = model;
        }

        public boolean isConfigured() {
            return apiKey != null && !apiKey.isBlank();
        }
    }

    public static class RagCredentials {
        private String url;
        private String apiKey;

        public String getUrl() {
            return url;
        }

        public void setUrl(String url) {
            this.url = url;
        }

        public String getApiKey() {
            return apiKey;
        }

        public void setApiKey(String apiKey) {
            this.apiKey = apiKey;
        }

        public boolean isConfigured() {
            return url != null && !url.isBlank();
        }
    }

    public static class TestUsersCredentials {
        private UserCredentials admin = new UserCredentials();
        private UserCredentials regular = new UserCredentials();

        public UserCredentials getAdmin() {
            return admin;
        }

        public void setAdmin(UserCredentials admin) {
            this.admin = admin;
        }

        public UserCredentials getRegular() {
            return regular;
        }

        public void setRegular(UserCredentials regular) {
            this.regular = regular;
        }
    }

    public static class UserCredentials {
        private String username;
        private String password;

        public String getUsername() {
            return username;
        }

        public void setUsername(String username) {
            this.username = username;
        }

        public String getPassword() {
            return password;
        }

        public void setPassword(String password) {
            this.password = password;
        }
    }
}
