from __future__ import annotations

import unittest
from unittest.mock import patch

from app.domain.application_planning_interaction import ApplicationPlanningInteraction
from app.graph.application_planning_interrupts import (
    application_planning_review_payload,
    resume_application_planning_review,
    validate_application_planning_review_action,
)


def _submission(
    artifact: str,
    action: str,
    *,
    request: str = "",
    answers: dict | None = None,
    ui_action: dict | None = None,
) -> ApplicationPlanningInteraction:
    """构造通过信封校验的审阅动作，供 action-stage 组合测试复用。"""

    return ApplicationPlanningInteraction.model_validate(
        {
            "gateId": "gate",
            "artifact": artifact,
            "artifactRevision": "revision",
            "action": action,
            "request": request,
            "answers": answers or {},
            **({"uiAction": ui_action} if ui_action is not None else {}),
        }
    )


class ApplicationPlanningStructuredActionTests(unittest.TestCase):
    """验证正式产物审阅门只接受当前阶段允许的结构化动作。"""

    def test_answer_is_rejected_on_confirmation_card(self) -> None:
        """确认卡不能把 answer 误当成澄清回答。"""

        with self.assertRaisesRegex(ValueError, "不允许 action=answer"):
            validate_application_planning_review_action(
                {
                    "clarification": {
                        "status": "requires_user_input",
                        "mode": "requirement_document_confirmation",
                        "questions": [{"id": "confirmation"}],
                    }
                },
                "requirement_document",
                _submission("requirement_document", "answer", answers={"answer": "继续"}),
            )

    def test_confirm_is_rejected_on_generation_error_card(self) -> None:
        """生成失败恢复卡必须使用 revise，不能伪装成正式确认。"""

        with self.assertRaisesRegex(ValueError, "clarification.mode"):
            validate_application_planning_review_action(
                {
                    "clarification": {
                        "status": "requires_user_input",
                        "mode": "technical_plan_generation_error",
                        "questions": [],
                    }
                },
                "technical_planning",
                _submission(
                    "technical_plan",
                    "confirm",
                    request="确认技术规划，只保留首页",
                ),
            )

    def test_ui_action_is_rejected_outside_ui_review(self) -> None:
        """UI 子动作不能在 ProductPlan 确认阶段执行。"""

        with self.assertRaises(ValueError):
            _submission(
                "requirement_document",
                "ui_action",
                ui_action={"action": "skip"},
            )

    def test_design_change_is_allowed_from_any_review_gate(self) -> None:
        """设计聊天动作可以从任一正式产物审阅门进入统一意图路由。"""

        validate_application_planning_review_action(
            {
                "clarification": {
                    "status": "requires_user_input",
                    "mode": "technical_plan_generation_error",
                    "questions": [],
                }
            },
            "technical_planning",
            _submission(
                "technical_plan",
                "design_change",
                request="把首页改成深色数据看板",
            ),
        )

    def test_planning_stage_entry_only_accepts_explicit_enter_action(self) -> None:
        """UI 完成后的入口门禁不能把普通确认误当成进入规划阶段。"""

        state = {
            "ui_designs": {"confirmation_status": "skipped"},
            "clarification": {
                "status": "completed",
                "mode": "ui_design_confirmation",
                "questions": [],
            }
        }
        validate_application_planning_review_action(
            state,
            "planning_stage_entry",
            _submission("ui_designs", "enter_planning"),
        )
        with self.assertRaisesRegex(ValueError, "只允许 action=enter_planning"):
            validate_application_planning_review_action(
                state,
                "planning_stage_entry",
                _submission("ui_designs", "confirm"),
            )

    def test_generation_error_retry_preserves_failed_candidate(self) -> None:
        """生成失败卡的 revise 必须保留 repair candidate 并继续既有修复路由。"""

        failed_candidate = {"artifact_type": "technical-plan", "marker": "failed-v2"}
        state = {
            "technical_plan": {"artifact_type": "technical-plan", "marker": "v1"},
            "technical_plan_repair_candidate": failed_candidate,
            "technical_plan_repair_errors": ["schema invalid"],
            "clarification": {
                "status": "requires_user_input",
                "mode": "technical_plan_generation_error",
                "questions": [],
            },
        }
        payload = application_planning_review_payload(state, "technical_planning")
        submission = {
            "gateId": payload["gateId"],
            "artifact": payload["artifact"],
            "artifactRevision": payload["artifactRevision"],
            "action": "revise",
            "request": "请继续修复技术规划",
        }

        with patch(
            "app.graph.application_planning_interrupts.interrupt",
            return_value=submission,
        ):
            command = resume_application_planning_review(
                state,
                "technical_planning",
            )

        self.assertNotIn("technical_plan_repair_candidate", command.update)
        self.assertNotIn("technical_plan_repair_errors", command.update)
        self.assertNotIn("design_change_submission", command.update)


if __name__ == "__main__":
    unittest.main()
