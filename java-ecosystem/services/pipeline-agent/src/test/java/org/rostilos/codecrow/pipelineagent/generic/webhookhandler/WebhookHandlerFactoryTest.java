package org.rostilos.codecrow.pipelineagent.generic.webhookhandler;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.pipelineagent.generic.dto.webhook.WebhookPayload;

import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.function.Consumer;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.*;

@DisplayName("WebhookHandlerFactory")
class WebhookHandlerFactoryTest {

    private WebhookHandlerFactory factory;

    @Nested
    @DisplayName("Constructor")
    class ConstructorTests {

        @Test
        @DisplayName("should initialize with empty handler list")
        void shouldInitializeWithEmptyHandlerList() {
            factory = new WebhookHandlerFactory(List.of());

            assertThat(factory.hasHandlerForProvider(EVcsProvider.GITHUB)).isFalse();
            assertThat(factory.hasHandlerForProvider(EVcsProvider.BITBUCKET_CLOUD)).isFalse();
            assertThat(factory.hasHandlerForProvider(EVcsProvider.GITLAB)).isFalse();
        }

        @Test
        @DisplayName("should group handlers by provider")
        void shouldGroupHandlersByProvider() {
            WebhookHandler githubHandler = createMockHandler(EVcsProvider.GITHUB, "push");
            WebhookHandler bitbucketHandler = createMockHandler(EVcsProvider.BITBUCKET_CLOUD, "pullrequest:created");

            factory = new WebhookHandlerFactory(List.of(githubHandler, bitbucketHandler));

            assertThat(factory.hasHandlerForProvider(EVcsProvider.GITHUB)).isTrue();
            assertThat(factory.hasHandlerForProvider(EVcsProvider.BITBUCKET_CLOUD)).isTrue();
        }

        @Test
        @DisplayName("should separate multi-provider handlers")
        void shouldSeparateMultiProviderHandlers() {
            WebhookHandler multiHandler = createMockHandler(null, "common_event");
            WebhookHandler githubHandler = createMockHandler(EVcsProvider.GITHUB, "push");

            factory = new WebhookHandlerFactory(List.of(multiHandler, githubHandler));

            // Multi-provider handler should make all providers return true
            assertThat(factory.hasHandlerForProvider(EVcsProvider.GITHUB)).isTrue();
            assertThat(factory.hasHandlerForProvider(EVcsProvider.BITBUCKET_CLOUD)).isTrue();
            assertThat(factory.hasHandlerForProvider(EVcsProvider.GITLAB)).isTrue();
        }
    }

    @Nested
    @DisplayName("getHandler()")
    class GetHandlerTests {

        @Test
        @DisplayName("should return handler for matching provider and event")
        void shouldReturnHandlerForMatchingProviderAndEvent() {
            WebhookHandler githubPushHandler = createMockHandler(EVcsProvider.GITHUB, "push");
            factory = new WebhookHandlerFactory(List.of(githubPushHandler));

            Optional<WebhookHandler> result = factory.getHandler(EVcsProvider.GITHUB, "push");

            assertThat(result).isPresent();
            assertThat(result.get()).isEqualTo(githubPushHandler);
        }

        @Test
        @DisplayName("should return empty for non-matching event")
        void shouldReturnEmptyForNonMatchingEvent() {
            WebhookHandler githubPushHandler = createMockHandler(EVcsProvider.GITHUB, "push");
            factory = new WebhookHandlerFactory(List.of(githubPushHandler));

            Optional<WebhookHandler> result = factory.getHandler(EVcsProvider.GITHUB, "pull_request");

            assertThat(result).isEmpty();
        }

        @Test
        @DisplayName("should return empty for non-matching provider")
        void shouldReturnEmptyForNonMatchingProvider() {
            WebhookHandler githubPushHandler = createMockHandler(EVcsProvider.GITHUB, "push");
            factory = new WebhookHandlerFactory(List.of(githubPushHandler));

            Optional<WebhookHandler> result = factory.getHandler(EVcsProvider.BITBUCKET_CLOUD, "push");

            assertThat(result).isEmpty();
        }

        @Test
        @DisplayName("should prefer provider-specific handler over multi-provider")
        void shouldPreferProviderSpecificHandlerOverMultiProvider() {
            WebhookHandler githubSpecific = createMockHandler(EVcsProvider.GITHUB, "push");
            WebhookHandler multiProvider = createMockHandler(null, "push");
            factory = new WebhookHandlerFactory(List.of(multiProvider, githubSpecific));

            Optional<WebhookHandler> result = factory.getHandler(EVcsProvider.GITHUB, "push");

            assertThat(result).isPresent();
            assertThat(result.get()).isEqualTo(githubSpecific);
        }

        @Test
        @DisplayName("should fall back to multi-provider handler")
        void shouldFallBackToMultiProviderHandler() {
            WebhookHandler multiProvider = createMockHandler(null, "push");
            factory = new WebhookHandlerFactory(List.of(multiProvider));

            Optional<WebhookHandler> result = factory.getHandler(EVcsProvider.BITBUCKET_CLOUD, "push");

            assertThat(result).isPresent();
            assertThat(result.get()).isEqualTo(multiProvider);
        }

        @Test
        @DisplayName("should return first matching handler when multiple match")
        void shouldReturnFirstMatchingHandlerWhenMultipleMatch() {
            WebhookHandler handler1 = createMockHandler(EVcsProvider.GITHUB, "push");
            WebhookHandler handler2 = createMockHandler(EVcsProvider.GITHUB, "push");
            factory = new WebhookHandlerFactory(List.of(handler1, handler2));

            Optional<WebhookHandler> result = factory.getHandler(EVcsProvider.GITHUB, "push");

            assertThat(result).isPresent();
            assertThat(result.get()).isEqualTo(handler1);
        }
    }

    @Nested
    @DisplayName("hasHandlerForProvider()")
    class HasHandlerForProviderTests {

        @Test
        @DisplayName("should return true when provider has handlers")
        void shouldReturnTrueWhenProviderHasHandlers() {
            WebhookHandler githubHandler = createMockHandler(EVcsProvider.GITHUB, "push");
            factory = new WebhookHandlerFactory(List.of(githubHandler));

            assertThat(factory.hasHandlerForProvider(EVcsProvider.GITHUB)).isTrue();
        }

        @Test
        @DisplayName("should return false when provider has no handlers")
        void shouldReturnFalseWhenProviderHasNoHandlers() {
            WebhookHandler githubHandler = createMockHandler(EVcsProvider.GITHUB, "push");
            factory = new WebhookHandlerFactory(List.of(githubHandler));

            assertThat(factory.hasHandlerForProvider(EVcsProvider.BITBUCKET_CLOUD)).isFalse();
        }

        @Test
        @DisplayName("should return true when multi-provider handlers exist")
        void shouldReturnTrueWhenMultiProviderHandlersExist() {
            WebhookHandler multiHandler = createMockHandler(null, "event");
            factory = new WebhookHandlerFactory(List.of(multiHandler));

            assertThat(factory.hasHandlerForProvider(EVcsProvider.GITHUB)).isTrue();
            assertThat(factory.hasHandlerForProvider(EVcsProvider.BITBUCKET_CLOUD)).isTrue();
            assertThat(factory.hasHandlerForProvider(EVcsProvider.GITLAB)).isTrue();
        }
    }

    @Nested
    @DisplayName("getHandlersForProvider()")
    class GetHandlersForProviderTests {

        @Test
        @DisplayName("should return provider-specific handlers")
        void shouldReturnProviderSpecificHandlers() {
            WebhookHandler github1 = createMockHandler(EVcsProvider.GITHUB, "push");
            WebhookHandler github2 = createMockHandler(EVcsProvider.GITHUB, "pull_request");
            WebhookHandler bitbucket = createMockHandler(EVcsProvider.BITBUCKET_CLOUD, "push");
            factory = new WebhookHandlerFactory(List.of(github1, github2, bitbucket));

            List<WebhookHandler> result = factory.getHandlersForProvider(EVcsProvider.GITHUB);

            assertThat(result).hasSize(2);
            assertThat(result).contains(github1, github2);
            assertThat(result).doesNotContain(bitbucket);
        }

        @Test
        @DisplayName("should include multi-provider handlers")
        void shouldIncludeMultiProviderHandlers() {
            WebhookHandler githubHandler = createMockHandler(EVcsProvider.GITHUB, "push");
            WebhookHandler multiHandler = createMockHandler(null, "common");
            factory = new WebhookHandlerFactory(List.of(githubHandler, multiHandler));

            List<WebhookHandler> result = factory.getHandlersForProvider(EVcsProvider.GITHUB);

            assertThat(result).hasSize(2);
            assertThat(result).contains(githubHandler, multiHandler);
        }

        @Test
        @DisplayName("should return only multi-provider handlers when no specific handlers")
        void shouldReturnOnlyMultiProviderHandlersWhenNoSpecific() {
            WebhookHandler multiHandler = createMockHandler(null, "common");
            factory = new WebhookHandlerFactory(List.of(multiHandler));

            List<WebhookHandler> result = factory.getHandlersForProvider(EVcsProvider.GITLAB);

            assertThat(result).hasSize(1);
            assertThat(result).contains(multiHandler);
        }

        @Test
        @DisplayName("should return empty list when no handlers available")
        void shouldReturnEmptyListWhenNoHandlersAvailable() {
            factory = new WebhookHandlerFactory(List.of());

            List<WebhookHandler> result = factory.getHandlersForProvider(EVcsProvider.GITHUB);

            assertThat(result).isEmpty();
        }

        @Test
        @DisplayName("should return mutable list")
        void shouldReturnMutableList() {
            WebhookHandler handler = createMockHandler(EVcsProvider.GITHUB, "push");
            factory = new WebhookHandlerFactory(List.of(handler));

            List<WebhookHandler> result = factory.getHandlersForProvider(EVcsProvider.GITHUB);
            WebhookHandler newHandler = createMockHandler(EVcsProvider.GITHUB, "pr");

            // Should not throw
            result.add(newHandler);
            assertThat(result).hasSize(2);
        }
    }

    @Nested
    @DisplayName("Integration Scenarios")
    class IntegrationScenarios {

        @Test
        @DisplayName("should handle mixed provider setup")
        void shouldHandleMixedProviderSetup() {
            WebhookHandler githubPush = createMockHandler(EVcsProvider.GITHUB, "push");
            WebhookHandler githubPR = createMockHandler(EVcsProvider.GITHUB, "pull_request");
            WebhookHandler bitbucketPush = createMockHandler(EVcsProvider.BITBUCKET_CLOUD, "repo:push");
            WebhookHandler bitbucketPR = createMockHandler(EVcsProvider.BITBUCKET_CLOUD, "pullrequest:created");
            WebhookHandler gitlabPush = createMockHandler(EVcsProvider.GITLAB, "Push Hook");
            WebhookHandler commonHandler = createMockHandler(null, "ping");

            factory = new WebhookHandlerFactory(List.of(
                    githubPush, githubPR,
                    bitbucketPush, bitbucketPR,
                    gitlabPush,
                    commonHandler
            ));

            // GitHub handlers
            assertThat(factory.getHandler(EVcsProvider.GITHUB, "push")).contains(githubPush);
            assertThat(factory.getHandler(EVcsProvider.GITHUB, "pull_request")).contains(githubPR);
            assertThat(factory.getHandler(EVcsProvider.GITHUB, "ping")).contains(commonHandler);

            // Bitbucket handlers
            assertThat(factory.getHandler(EVcsProvider.BITBUCKET_CLOUD, "repo:push")).contains(bitbucketPush);
            assertThat(factory.getHandler(EVcsProvider.BITBUCKET_CLOUD, "pullrequest:created")).contains(bitbucketPR);
            assertThat(factory.getHandler(EVcsProvider.BITBUCKET_CLOUD, "ping")).contains(commonHandler);

            // GitLab handlers
            assertThat(factory.getHandler(EVcsProvider.GITLAB, "Push Hook")).contains(gitlabPush);
            assertThat(factory.getHandler(EVcsProvider.GITLAB, "ping")).contains(commonHandler);
        }
    }

    // Helper method to create mock handlers
    private WebhookHandler createMockHandler(EVcsProvider provider, String supportedEvent) {
        return new WebhookHandler() {
            @Override
            public EVcsProvider getProvider() {
                return provider;
            }

            @Override
            public boolean supportsEvent(String eventType) {
                return supportedEvent.equals(eventType);
            }

            @Override
            public WebhookResult handle(WebhookPayload payload, Project project,
                                        Consumer<Map<String, Object>> eventConsumer) {
                return WebhookResult.success("Processed");
            }
        };
    }
}
