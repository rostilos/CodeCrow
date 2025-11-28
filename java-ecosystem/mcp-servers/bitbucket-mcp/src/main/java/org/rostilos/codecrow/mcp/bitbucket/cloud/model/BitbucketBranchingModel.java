package org.rostilos.codecrow.mcp.bitbucket.cloud.model;

import java.util.List;
import java.util.Map;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.databind.JsonNode;

@JsonIgnoreProperties(ignoreUnknown = true)
public class BitbucketBranchingModel {
    public String type;
    public Development development;
    public Production production;
    @JsonProperty("branch_types")
    public List<BranchType> branchTypes;
    public Map<String, JsonNode> links;

    public static class Development {
        public String name;
        public BitbucketBranch branch;
        @JsonProperty("use_mainbranch")
        public boolean useMainbranch;
    }

    public static class Production {
        public String name;
        public BitbucketBranch branch;
        @JsonProperty("use_mainbranch")
        public boolean useMainbranch;
    }

    public static class BranchType {
        public String kind;
        public String prefix;
    }
}
