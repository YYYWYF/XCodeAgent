"""前端代码审查 DeepAgent 声明入口。"""

from app.agents.code_analyze.agent import create_code_analyze_agent
from app.agents.code_analyze.runner import run_frontend_code_analysis

__all__ = ["create_code_analyze_agent", "run_frontend_code_analysis"]
