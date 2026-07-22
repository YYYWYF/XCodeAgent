from __future__ import annotations

import json
from typing import Any

from app.agents.messages import last_agent_text
from app.config import Settings


def _test_validation_prompt(
    *,
    test_results: list[dict[str, Any]],
    build_results: list[dict[str, Any]],
) -> str:
    return (
        "You are the Test Agent in an app-generation workflow.\n"
        "Review deterministic test evidence and return a concise validation report. "
        "Do not mark failed checks as passed. If any check fails, explain what should "
        "be returned to the Main Agent as a revision request. Use stdout_tail and "
        "stderr_tail first; virtual log paths are readable from the workspace root. "
        "If neither summary nor readable log contains the cause, state that evidence "
        "is insufficient and do not guess a root cause.\n\n"
        f"Deterministic test results:\n{json.dumps(test_results, ensure_ascii=False, indent=2)}\n\n"
        f"Build results:\n{json.dumps(build_results, ensure_ascii=False, indent=2)}"
    )


def _invoke_live_test_agent(
    *,
    test_results: list[dict[str, Any]],
    build_results: list[dict[str, Any]],
    workspace: str | None = None,
    selected_skill_names: list[str] | None = None,
) -> str:
    """使用本次工作流的技能白名单调用测试 Deep Agent。"""

    # 延迟创建可确保 Agent 的工作区和技能权限只属于本次运行。
    from app.agents import create_agent_bundle

    result = create_agent_bundle(workspace, selected_skill_names).test.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": _test_validation_prompt(
                        test_results=test_results,
                        build_results=build_results,
                    ),
                }
            ]
        }
    )
    return last_agent_text(result)


def summarize_tests_with_deep_agent(
    *,
    test_results: list[dict[str, Any]],
    build_results: list[dict[str, Any]],
    workspace: str | None = None,
    selected_skill_names: list[str] | None = None,
) -> dict[str, Any]:
    """让 Test Deep Agent 在相同技能集合下总结确定性测试结果。"""

    settings = Settings.from_env()
    agent_note = _invoke_live_test_agent(
        test_results=test_results,
        build_results=build_results,
        workspace=workspace,
        selected_skill_names=selected_skill_names,
    )
    return {
        "agent_note": agent_note,
        "reviewed_by": {
            "agent": "test-agent",
            "mode": "live",
            "model": settings.model_name,
            "source": "test_deep_agent",
            "requiredSkillsLoaded": list(selected_skill_names or []),
        },
    }
