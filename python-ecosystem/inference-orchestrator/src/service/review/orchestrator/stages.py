"""
Stage execution entry point — re-exports all public stage functions.

Each stage lives in its own module:
  - branch_analysis.py   → branch analysis & reconciliation
  - stage_0_planning.py  → PR planning & file prioritisation
  - stage_1_file_review.py → parallel batch file reviews
  - stage_2_cross_file.py  → cross-file architectural analysis
  - stage_3_aggregation.py → executive summary & MCP verification
  - stage_helpers.py       → shared helpers (event emission, rules, RAG filtering)
"""

from service.review.orchestrator.branch_analysis import (       
    execute_branch_analysis,
    execute_branch_reconciliation_direct,
)
from service.review.orchestrator.stage_0_planning import (      
    execute_stage_0_planning,
)
from service.review.orchestrator.stage_1_file_review import (   
    execute_stage_1_file_reviews,
)
from service.review.orchestrator.stage_2_cross_file import (    
    execute_stage_2_cross_file,
)
from service.review.orchestrator.stage_3_aggregation import (   
    execute_stage_3_aggregation,
)
from service.review.orchestrator.stage_helpers import (         
    emit_status as _emit_status,
    emit_progress as _emit_progress,
    emit_error as _emit_error,
)
