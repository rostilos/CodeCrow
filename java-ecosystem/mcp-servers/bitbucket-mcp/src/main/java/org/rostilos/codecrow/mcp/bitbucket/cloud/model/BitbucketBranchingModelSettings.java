package org.rostilos.codecrow.mcp.bitbucket.cloud.model;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;
import java.util.Map;

public class BitbucketBranchingModelSettings {
    public String type;
    public Development development;
    public Production production;
    @JsonProperty("branch_types")
    public List<BranchType> branchTypes;
    public Map<String, BitbucketLink[]> links;

    public static class Development {
        public String name;
        @JsonProperty("use_mainbranch")
        public boolean useMainbranch;
        @JsonProperty("is_valid")
        public Boolean isValid;
    }

    public static class Production {
        public String name;
        @JsonProperty("use_mainbranch")
        public boolean useMainbranch;
        public boolean enabled;
        @JsonProperty("is_valid")
        public Boolean isValid;
    }

    public static class BranchType {
        public String kind;
        public String prefix;
        public boolean enabled;
    }
}
