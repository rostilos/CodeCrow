package org.rostilos.codecrow.mcp.bitbucket.cloud.model;

import java.util.Map;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

public class BitbucketBranchReference {
    public Branch branch;
    public Commit commit;
    public BitbucketRepository repository;

    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class Branch {
        public String name;
        public Map<String, BitbucketLink> links;
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class Commit {
        public String hash;
        public Map<String, BitbucketLink> links;
    }
}
