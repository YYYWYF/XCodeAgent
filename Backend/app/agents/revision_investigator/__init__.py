"""二次修改只读调查 Agent。"""

from app.agents.revision_investigator.agent import (
    create_revision_investigator_agent,
    revision_investigator_prompt,
)

__all__ = [
    "create_revision_investigator_agent",
    "revision_investigator_prompt",
]
