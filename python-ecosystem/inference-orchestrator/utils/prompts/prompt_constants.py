"""
Prompt constants entry point — re-exports all templates for backward compatibility.

Each domain has its own module:
  - constants_shared.py   → categories, line-number rules, dedup, diff format, agent instructions
  - constants_branch.py   → branch analysis & reconciliation templates
  - constants_stage_0.py  → planning prompt
  - constants_stage_1.py  → batch file review prompt
  - constants_stage_2.py  → cross-file analysis prompt
  - constants_stage_3.py  → aggregation / executive summary prompt
  - constants_mcp.py      → conditional MCP tool sections
"""

from utils.prompts.constants_shared import (    # noqa: F401
    ISSUE_CATEGORIES,
    CODE_SNIPPET_AND_SCOPE_INSTRUCTIONS,
    ISSUE_DEDUPLICATION_INSTRUCTIONS,
    SUGGESTED_FIX_DIFF_FORMAT,
    ADDITIONAL_INSTRUCTIONS,
)

# Backward-compatible alias
LINE_NUMBER_INSTRUCTIONS = CODE_SNIPPET_AND_SCOPE_INSTRUCTIONS
from utils.prompts.constants_branch import (    # noqa: F401
    BRANCH_REVIEW_PROMPT_TEMPLATE,
    BRANCH_RECONCILIATION_DIRECT_PROMPT_TEMPLATE,
)
from utils.prompts.constants_stage_0 import (   # noqa: F401
    STAGE_0_PLANNING_PROMPT_TEMPLATE,
)
from utils.prompts.constants_stage_1 import (   # noqa: F401
    STAGE_1_BATCH_PROMPT_TEMPLATE,
)
from utils.prompts.constants_stage_2 import (   # noqa: F401
    STAGE_2_CROSS_FILE_PROMPT_TEMPLATE,
)
from utils.prompts.constants_stage_3 import (   # noqa: F401
    STAGE_3_AGGREGATION_PROMPT_TEMPLATE,
)
from utils.prompts.constants_mcp import (       # noqa: F401
    STAGE_1_MCP_TOOL_SECTION,
    STAGE_3_MCP_VERIFICATION_SECTION,
)
