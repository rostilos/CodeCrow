package org.rostilos.codecrow.email.config;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.context.annotation.Configuration;

@Configuration
@ConfigurationProperties(prefix = "codecrow.email")
public class EmailProperties {
    
    private boolean enabled = true;
    private String from = "noreply@codecrow.io";
    private String fromName = "CodeCrow";
    private String frontendUrl = "http://localhost:8080";
    private String appName = "CodeCrow";
    
    public boolean isEnabled() {
        return enabled;
    }
    
    public void setEnabled(boolean enabled) {
        this.enabled = enabled;
    }
    
    public String getFrom() {
        return from;
    }
    
    public void setFrom(String from) {
        this.from = from;
    }
    
    public String getFromName() {
        return fromName;
    }
    
    public void setFromName(String fromName) {
        this.fromName = fromName;
    }
    
    public String getFrontendUrl() {
        return frontendUrl;
    }
    
    public void setFrontendUrl(String frontendUrl) {
        this.frontendUrl = frontendUrl;
    }
    
    public String getAppName() {
        return appName;
    }
    
    public void setAppName(String appName) {
        this.appName = appName;
    }
}
