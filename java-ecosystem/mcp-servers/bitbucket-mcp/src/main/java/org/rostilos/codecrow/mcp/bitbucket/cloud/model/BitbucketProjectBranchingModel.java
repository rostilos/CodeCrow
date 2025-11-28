package org.rostilos.codecrow.mcp.bitbucket.cloud.model;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;
import java.util.Map;

public class BitbucketProjectBranchingModel {
    public String type; // "project_branching_model"
    public Development development;
    public Production production;
    @JsonProperty("branch_types")
    public List<BranchType> branchTypes;
    public Map<String, BitbucketLink[]> links;

    public static class Development {
        public String name;
        @JsonProperty("use_mainbranch")
        public boolean useMainbranch;
    }

    public static class Production {
        public String name;
        @JsonProperty("use_mainbranch")
        public boolean useMainbranch;
    }

    public static class BranchType {
        public String kind;
        public String prefix;
    }
}
