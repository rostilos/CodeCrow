"""
Prompt template for Stage 0: Planning & Prioritization.
"""

STAGE_0_PLANNING_PROMPT_TEMPLATE = """SYSTEM ROLE:
You are an expert PR scope analyzer for a code review system.
Your job is to prioritize files for review - ALL files must be included.
Output structured JSON—no filler, no explanations.

---

USER PROMPT:

Task: Analyze this PR and create a review plan for ALL changed files.

## PR Metadata
- Repository: {repo_slug}
- PR ID: {pr_id}
- Title: {pr_title}
- Author: {author}
- Branch: {branch_name}
- Target: {target_branch}
- Commit: {commit_hash}

## Changed Files Summary
```json
{changed_files_json}
```

Business Context
This PR introduces changes that need careful analysis.

CRITICAL INSTRUCTION:
You MUST include EVERY file from the "Changed Files Summary" above.
- Files that need review go into "file_groups"
- Files that can be skipped go into "files_to_skip" with a reason
- The total count of files in file_groups + files_to_skip MUST equal the input file count

Create a prioritized review plan in this JSON format:

{{
  "analysis_summary": "overview of PR scope and risk level",
  "file_groups": [
    {{
      "group_id": "GROUP_A_SECURITY",
      "priority": "CRITICAL",
      "rationale": "reason this group is critical",
      "files": [
        {{
          "path": "exact/path/from/input",
          "focus_areas": ["SECURITY", "AUTHORIZATION"],
          "risk_level": "HIGH",
          "estimated_issues": 2
        }}
      ]
    }},
    {{
      "group_id": "GROUP_B_ARCHITECTURE",
      "priority": "HIGH",
      "rationale": "...",
      "files": [...]
    }},
    {{
      "group_id": "GROUP_C_MEDIUM",
      "priority": "MEDIUM",
      "rationale": "...",
      "files": [...]
    }},
    {{
      "group_id": "GROUP_D_LOW",
      "priority": "LOW",
      "rationale": "tests, config, docs",
      "files": [...]
    }}
  ],
  "files_to_skip": [
    {{
      "path": "exact/path/from/input",
      "reason": "Auto-generated file / lock file / no logic"
    }}
  ],
  "cross_file_concerns": [
    "Hypothesis 1: check if X affects Y",
    "Hypothesis 2: check security of Z"
  ]
}}

Priority Guidelines:
- CRITICAL: security, auth, data access, payment, core business logic
- HIGH: architecture changes, API contracts, database schemas
- MEDIUM: refactoring, new utilities, business logic extensions  
- LOW: tests, documentation, config files, styling
- files_to_skip: lock files, auto-generated code, binary assets, .md files (unless README changes are significant)

REMEMBER: Every input file must appear exactly once - either in a file_group or in files_to_skip.
"""
