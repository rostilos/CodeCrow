package org.rostilos.codecrow.pipelineagent.generic.controller;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.service.*;
import org.rostilos.codecrow.pipelineagent.bitbucket.webhookhandler.BitbucketCloudWebhookParser;
import org.rostilos.codecrow.pipelineagent.github.webhookhandler.GitHubWebhookParser;
import org.rostilos.codecrow.pipelineagent.gitlab.webhookhandler.GitLabWebhookParser;
import org.rostilos.codecrow.pipelineagent.generic.processor.WebhookAsyncProcessor;
import org.rostilos.codecrow.pipelineagent.generic.service.*;
import org.rostilos.codecrow.pipelineagent.generic.webhookhandler.*;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import java.util.Optional;
import java.util.function.Predicate;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.util.HexFormat;
import java.nio.charset.StandardCharsets;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

class WebhookAuthenticationBoundaryTest {
    @ParameterizedTest @CsvSource({
            "github,X-GitHub-Event,pull_request", "github,X-GitHub-Event,push", "github,X-GitHub-Event,issue_comment",
            "gitlab,X-Gitlab-Event,Merge Request Hook", "gitlab,X-Gitlab-Event,Push Hook", "gitlab,X-Gitlab-Event,Note Hook",
            "bitbucket-cloud,X-Event-Key,pullrequest:created", "bitbucket-cloud,X-Event-Key,repo:push", "bitbucket-cloud,X-Event-Key,pullrequest:comment_created"})
    void everySupportedProviderEventAuthenticatesBeforeAnyWorkAndTokenRouteRemainsCompatible(
            String provider, String eventHeader, String event) throws Exception {
        var resolver=mock(WebhookProjectResolver.class); var project=mock(Project.class);
        var jobs=mock(JobService.class); var async=mock(WebhookAsyncProcessor.class);
        var handlers=mock(WebhookHandlerFactory.class); var settings=mock(SiteSettingsProvider.class);
        when(project.getId()).thenReturn(7L);
        when(resolver.webhookSecret(project)).thenReturn("secret");
        when(resolver.validateWebhookAuth(project,"secret")).thenReturn(true);
        when(resolver.findAuthenticatedProject(any(),any(),any(),any(),any())).thenAnswer(call -> {
            Predicate<Project> predicate=call.getArgument(4);
            return predicate.test(project) ? Optional.of(project) : Optional.empty();
        });
        var controller=new ProviderWebhookController(resolver,new BitbucketCloudWebhookParser(),
                new GitHubWebhookParser(),new GitLabWebhookParser(),new ObjectMapper(),handlers,jobs,async,
                settings,mock(WebhookDeduplicationService.class),new NativeWebhookAuthenticator(resolver,settings));
        var mvc=MockMvcBuilders.standaloneSetup(controller).build();
        String body="{\"repository\":{\"id\":42,\"uuid\":\"{42}\",\"name\":\"repo\",\"owner\":{\"login\":\"owner\"},\"workspace\":{\"slug\":\"owner\"}},\"project\":{\"id\":42,\"path\":\"repo\",\"path_with_namespace\":\"owner/repo\"},\"message\":\"київ\"}";
        var unsigned=post("/api/webhooks/"+provider).contentType("application/json").header(eventHeader,event).content(body.getBytes(StandardCharsets.UTF_8));
        mvc.perform(unsigned).andExpect(status().isUnauthorized());
        verifyNoInteractions(jobs,async,handlers);
        Mac mac=Mac.getInstance("HmacSHA256");mac.init(new SecretKeySpec("secret".getBytes(StandardCharsets.UTF_8),"HmacSHA256"));
        String signature="sha256="+HexFormat.of().formatHex(mac.doFinal(body.getBytes(StandardCharsets.UTF_8)));
        var signed=post("/api/webhooks/"+provider).contentType("application/json").header(eventHeader,event)
                .header("X-Hub-Signature-256",signature).header("X-Hub-Signature",signature).header("X-Gitlab-Token","secret")
                .content(body.getBytes(StandardCharsets.UTF_8));
        mvc.perform(signed).andExpect(status().isOk()).andExpect(jsonPath("status").value("ignored"));
        mvc.perform(post("/api/webhooks/"+provider+"/secret").contentType("application/json")
                .header(eventHeader,event).content(body.getBytes(StandardCharsets.UTF_8)))
                .andExpect(status().isOk()).andExpect(jsonPath("status").value("ignored"));
        verify(handlers,times(2)).getHandler(eq(EVcsProvider.fromId(provider)),any(org.rostilos.codecrow.pipelineagent.generic.dto.webhook.WebhookPayload.class));
        verifyNoInteractions(jobs,async);
    }
}
