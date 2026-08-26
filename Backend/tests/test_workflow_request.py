from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.agents.test_generation.generator import _build_prompt
from app.graph.subgraphs.testing import collect_unit_test_targets
from app.protocols.workflow.request import (
    _build_execution_scope,
    _retry_failed_execution_node,
    workflow_run_inputs,
)


class WorkflowRequestTests(unittest.TestCase):
    def test_endpoint_selection_derives_endpoint_build_scope(self) -> None:
        """选择单个 endpoint 时，执行计划范围应锁定到该 endpoint 而非整应用。"""

        result = workflow_run_inputs(
            {
                "request": "确认接口详情，继续生成执行计划",
                "forwardedProps": {
                    "selectedApiContractId": "orders-api",
                    "selectedEndpointId": "orders.list",
                    "detailTargetType": "endpoint",
                },
            }
        )

        self.assertEqual(
            result["resume_values"]["build_execution_scope"],
            {
                "type": "endpoint",
                "targetId": "orders.list",
                "apiContractId": "orders-api",
            },
        )
        self.assertNotIn("selectedPageId", result["resume_values"])

    def test_endpoint_scope_restores_detail_target_ids(self) -> None:
        """正式 handoff 只提交 endpoint scope 时，后端仍应恢复详情确认所需的目标 ID。"""

        result = workflow_run_inputs(
            {
                "request": "用户已确认，请进入正式工作流处理该需求。",
                "forwardedProps": {
                    "buildExecutionScope": {
                        "type": "endpoint",
                        "targetId": "orders.list",
                        "apiContractId": "orders-api",
                    }
                },
            }
        )

        self.assertEqual(result["resume_values"]["selected_api_contract_id"], "orders-api")
        self.assertEqual(result["resume_values"]["selected_endpoint_id"], "orders.list")
        self.assertEqual(result["resume_values"]["detail_target_type"], "endpoint")
        self.assertNotIn("selectedPageId", result["resume_values"])

    def test_resume_state_restores_camel_case_endpoint_scope(self) -> None:
        """从公开 StateSnapshot 重新执行时，应恢复 camelCase endpoint scope。"""

        result = workflow_run_inputs(
            {
                "request": "重新执行 prepare_build_tasks",
                "forwardedProps": {
                    "resumeState": {
                        "state": {
                            "phase": "prepare_build_tasks",
                            "buildExecutionScope": {
                                "type": "endpoint",
                                "targetId": "orders.list",
                                "apiContractId": "orders-api",
                            },
                        }
                    }
                },
            }
        )

        self.assertEqual(
            result["resume_values"]["build_execution_scope"],
            {
                "type": "endpoint",
                "targetId": "orders.list",
                "apiContractId": "orders-api",
            },
        )

    def test_endpoint_scope_infers_unique_api_contract_from_project_plan(self) -> None:
        """调试 scope 遗漏 apiContractId 时，应从唯一 endpoint 归属自动补齐。"""

        scope = _build_execution_scope(
            {"buildExecutionScope": {"type": "endpoint", "targetId": "orders.list"}},
            forwarded_props={},
            resume_values={},
            selected_page_id="",
            selected_api_contract_id="",
            selected_endpoint_id="",
            project_plan={
                "api_contracts": [
                    {
                        "id": "orders-api",
                        "endpoints": [{"id": "orders.list"}],
                    }
                ]
            },
        )

        self.assertEqual(
            scope,
            {
                "type": "endpoint",
                "targetId": "orders.list",
                "apiContractId": "orders-api",
            },
        )

    def test_workbench_extracts_explicit_resume_execution_run_id(self) -> None:
        """继续执行应只把旧 runId 作为锁转移令牌，不依赖生命周期快照恢复 Graph。"""

        result = workflow_run_inputs(
            {
                "forwardedProps": {
                    "resumeExecutionRunId": "run-stopped",
                }
            }
        )

        self.assertEqual(
            result["resume_values"]["resume_execution_run_id"],
            "run-stopped",
        )

    def test_workbench_extracts_interaction_from_matching_run(self) -> None:
        """页面恢复只能提交原运行自身的交互令牌。"""

        result = workflow_run_inputs(
            {
                "message": "验收通过",
                "resumeState": {
                    "runId": "run-page-a",
                    "state": {
                        "lifecycle": {
                            "activeExecutions": {
                                "run-page-a": {
                                    "pendingInteraction": {
                                        "id": "accept-page-a",
                                        "basedOnRevision": 12,
                                    }
                                },
                                "run-page-b": {
                                    "pendingInteraction": {
                                        "id": "accept-page-b",
                                        "basedOnRevision": 13,
                                    }
                                },
                            }
                        }
                    },
                },
            }
        )

        self.assertEqual(
            result["resume_values"]["lifecycle_interaction_submission"],
            {
                "id": "accept-page-a",
                "basedOnRevision": 12,
                "runId": "run-page-a",
            },
        )

    def test_application_planning_does_not_extract_root_interaction_token(self) -> None:
        """初始化恢复不再从 lifecycle 根节点提交交互令牌。"""

        result = workflow_run_inputs({
            "message": "确认并继续",
            "workflowScope": "application_planning",
            "resumeState": {
                "state": {
                    "phase": "requirements",
                    "lifecycle": {
                        "initialization": {"stage": "ready_for_workbench"},
                        "pendingInteraction": {
                            "id": "interaction-1",
                            "basedOnRevision": 7,
                        },
                    },
                }
            },
        })

        self.assertNotIn("lifecycle_interaction_submission", result["resume_values"])
        self.assertNotIn("lifecycle", result["resume_values"])

    def test_application_planning_forwards_edited_requirement_spec(self) -> None:
        inputs = workflow_run_inputs(
            {
                "forwardedProps": {
                    "workflowScope": "application_planning",
                    "editedRequirementSpec": {
                        "app_info": {"name": "仓储管理应用"},
                        "pages": [],
                    },
                }
            }
        )

        self.assertEqual(
            inputs["resume_values"]["edited_requirement_spec"]["app_info"]["name"],
            "仓储管理应用",
        )

    def test_application_planning_forwards_feedback_separately_from_confirmation(self) -> None:
        inputs = workflow_run_inputs(
            {
                "forwardedProps": {
                    "workflowScope": "application_planning",
                    "requirementSpecFeedback": "建议后续关注移动端适配。",
                }
            }
        )

        self.assertEqual(
            inputs["resume_values"]["requirement_spec_feedback"],
            "建议后续关注移动端适配。",
        )

    def test_application_planning_clears_feedback_when_not_submitted(self) -> None:
        inputs = workflow_run_inputs(
            {
                "forwardedProps": {
                    "workflowScope": "application_planning",
                }
            }
        )

        self.assertEqual(inputs["resume_values"]["requirement_spec_feedback"], "")

    def test_application_planning_extracts_ui_design_select_template_action(self) -> None:
        inputs = workflow_run_inputs(
            {
                "forwardedProps": {
                    "workflowScope": "application_planning",
                    "applicationPlanningInteraction": {
                        "gateId": "ui_designs:revision-1",
                        "artifact": "ui_designs",
                        "artifactRevision": "revision-1",
                        "action": "ui_action",
                        "uiAction": {
                            "pageId": "order_list_page",
                            "action": "select_template",
                            "templateId": "commonTable",
                        },
                    },
                }
            }
        )

        self.assertEqual(
            inputs["application_planning_interaction"]["ui_action"],
            {"pageId": "order_list_page", "action": "select_template", "templateId": "commonTable"},
        )

    def test_application_planning_answer_preserves_original_requirement(self) -> None:
        """创建规划的结构化回答必须以真实原始需求生成恢复请求。"""

        inputs = workflow_run_inputs(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": "请根据本轮确认继续创建规划。",
                    }
                ],
                "forwardedProps": {
                    "workflowScope": "application_planning",
                    "originalRequest": "创建人员管理应用，HR 管理全部人员，普通用户管理本人信息。",
                    "applicationPlanningInteraction": {
                        "gateId": "requirement_spec:revision-1",
                        "artifact": "requirement_spec",
                        "artifactRevision": "revision-1",
                        "action": "answer",
                        "answers": {
                            "本人信息入口": {"selected": ["独立「我的信息」页"]}
                        },
                    },
                },
            }
        )

        interaction_request = inputs["application_planning_interaction"]["request"]
        self.assertIn("创建人员管理应用", interaction_request)
        self.assertIn("独立「我的信息」页", interaction_request)
        self.assertNotIn("原始需求：\n请根据本轮确认继续创建规划。", interaction_request)

    def test_permission_answer_uses_business_label_and_survives_recovery_message(self) -> None:
        """权限回答不应把内部问题 ID 暴露给需求模型，也不能被恢复文案覆盖。"""

        inputs = workflow_run_inputs(
            {
                "message": "请根据本轮确认继续创建规划。",
                "forwardedProps": {
                    "workflowScope": "application_planning",
                    "originalRequest": "创建人员管理应用，涉及权限控制。",
                    "applicationPlanningInteraction": {
                        "gateId": "requirement_spec:revision-1",
                        "artifact": "requirement_spec",
                        "artifactRevision": "revision-1",
                        "action": "answer",
                        "request": "请根据本轮确认继续创建规划。",
                        "answers": {
                            "authorization_data_scope_business": (
                                "管理员可以查看人员列表，普通用户只能查看和修改自己的基本信息"
                            )
                        },
                    },
                },
            }
        )

        interaction_request = inputs["application_planning_interaction"]["request"]
        self.assertIn("数据范围业务含义", interaction_request)
        self.assertIn("管理员可以查看人员列表", interaction_request)
        self.assertNotIn("authorization_data_scope_business", interaction_request)

    def test_application_planning_extracts_ui_design_regenerate_action(self) -> None:
        inputs = workflow_run_inputs(
            {
                "forwardedProps": {
                    "workflowScope": "application_planning",
                    "applicationPlanningInteraction": {
                        "gateId": "ui_designs:revision-1",
                        "artifact": "ui_designs",
                        "artifactRevision": "revision-1",
                        "action": "ui_action",
                        "uiAction": {
                            "pageId": "dashboard_page",
                            "action": "regenerate",
                        },
                    },
                }
            }
        )

        self.assertEqual(
            inputs["application_planning_interaction"]["ui_action"],
            {"pageId": "dashboard_page", "action": "regenerate"},
        )

    def test_application_planning_extracts_ui_design_skip_action(self) -> None:
        """跳过 UI 设计动作应通过当前 Workflow 输入解析器保留下来。"""

        inputs = workflow_run_inputs(
            {
                "forwardedProps": {
                    "workflowScope": "application_planning",
                    "applicationPlanningInteraction": {
                        "gateId": "ui_designs:revision-1",
                        "artifact": "ui_designs",
                        "artifactRevision": "revision-1",
                        "action": "ui_action",
                        "uiAction": {"action": "skip"},
                    },
                }
            }
        )

        self.assertEqual(
            inputs["application_planning_interaction"]["ui_action"],
            {"action": "skip"},
        )

    def test_application_planning_rejects_invalid_ui_design_action(self) -> None:
        # select_template 缺 templateId、未知 action、缺 pageId 均视为无动作
        for invalid in (
            {"pageId": "p1", "action": "select_template"},
            {"pageId": "p1", "action": "unknown"},
            {"action": "regenerate"},
        ):
            with self.assertRaisesRegex(ValueError, "uiAction"):
                workflow_run_inputs(
                    {
                        "forwardedProps": {
                            "workflowScope": "application_planning",
                            "applicationPlanningInteraction": {
                                "gateId": "ui_designs:revision-1",
                                "artifact": "ui_designs",
                                "artifactRevision": "revision-1",
                                "action": "ui_action",
                                "uiAction": invalid,
                            },
                        }
                    }
                )

    def test_main_workflow_ignores_edited_requirement_spec(self) -> None:
        inputs = workflow_run_inputs(
            {
                "forwardedProps": {
                    "workflowScope": "main",
                    "editedRequirementSpec": {"app_info": {"name": "不应接收"}},
                }
            }
        )

        self.assertNotIn("edited_requirement_spec", inputs["resume_values"])

    def test_normalizes_selected_skill_names_from_forwarded_props(self) -> None:
        inputs = workflow_run_inputs(
            {
                "request": "使用技能实现页面",
                "forwardedProps": {
                    "selectedSkillNames": [" beta ", "alpha", "alpha"],
                },
            }
        )

        self.assertEqual(inputs["selected_skill_names"], ["alpha", "beta"])
        self.assertIsNone(inputs["selected_skills_error"])

    def test_rejects_invalid_selected_skill_names_inside_workflow_lifecycle(self) -> None:
        inputs = workflow_run_inputs(
            {
                "request": "使用技能实现页面",
                "forwardedProps": {"selectedSkillNames": "alpha"},
            }
        )

        self.assertEqual(inputs["selected_skill_names"], [])
        self.assertEqual(inputs["selected_skills_error"].code, "invalid_selected_skills")

    def test_resume_preserves_selection_and_rejects_replacement(self) -> None:
        preserved = workflow_run_inputs(
            {
                "request": "继续",
                "forwardedProps": {
                    "resumeState": {"state": {"selectedSkillNames": ["alpha"]}}
                },
            }
        )
        conflict = workflow_run_inputs(
            {
                "request": "继续",
                "forwardedProps": {
                    "selectedSkillNames": ["beta"],
                    "resumeState": {"state": {"selectedSkillNames": ["alpha"]}},
                },
            }
        )

        self.assertEqual(preserved["selected_skill_names"], ["alpha"])
        self.assertEqual(conflict["selected_skills_error"].code, "selected_skill_conflict")

    def test_reads_workspace_root_from_forwarded_props(self) -> None:
        inputs = workflow_run_inputs(
            {
                "request": "创建一个库存管理系统",
                "forwardedProps": {
                    "workspaceRoot": "/Users/sbw/Downloads/test/manage",
                },
            }
        )

        self.assertEqual(inputs["workspace"], "/Users/sbw/Downloads/test/manage")

    def test_reads_supported_editor_mode_from_forwarded_props(self) -> None:
        frontend_inputs = workflow_run_inputs(
            {
                "request": "修改按钮文案",
                "forwardedProps": {"editorMode": "frontend"},
            }
        )
        backend_inputs = workflow_run_inputs(
            {
                "request": "修复接口",
                "forwardedProps": {"editorMode": "backend"},
            }
        )

        self.assertEqual(frontend_inputs["editor_mode"], "frontend")
        self.assertEqual(backend_inputs["editor_mode"], "backend")

    def test_rejects_unsupported_editor_mode(self) -> None:
        inputs = workflow_run_inputs(
            {
                "request": "修改代码",
                "forwardedProps": {"editorMode": "unknown"},
            }
        )

        self.assertEqual(inputs["editor_mode"], "")

    def test_reads_cancel_run_id_from_forwarded_props(self) -> None:
        inputs = workflow_run_inputs(
            {"forwardedProps": {"cancelRunId": "workflow-active-run"}}
        )

        self.assertEqual(inputs["cancel_run_id"], "workflow-active-run")

    def test_merges_clarification_answers_with_original_request(self) -> None:
        inputs = workflow_run_inputs(
            {
                "originalRequest": "帮我做一个库房系统",
                "clarificationAnswers": [
                    {
                        "question": "系统有哪些用户角色？",
                        "answer": ["普通员工", "库管员"],
                    },
                    {
                        "question": "核心功能有哪些？",
                        "answer": "入库管理、出库管理、库存查询",
                    },
                ],
            }
        )

        request = inputs["request"]
        self.assertIn("原始需求", request)
        self.assertIn("帮我做一个库房系统", request)
        self.assertIn("系统有哪些用户角色", request)
        self.assertIn("普通员工、库管员", request)
        self.assertIn("入库管理、出库管理、库存查询", request)
        self.assertNotIn("user_interaction_submission", inputs)

    def test_plain_recovery_message_is_not_a_user_interaction_submission(self) -> None:
        """普通恢复文案不得获得确认卡结构化提交权限。"""

        inputs = workflow_run_inputs(
            {
                "request": "请从上次保存的规划状态继续执行。",
                "forwardedProps": {"workflowScope": "application_planning"},
            }
        )

        self.assertNotIn("user_interaction_submission", inputs)

    def test_unit_test_confirmation_is_forwarded_as_resume_decision(self) -> None:
        """单元测试确认按钮必须转换为主 Workflow 可消费的 skip/run 状态。"""

        inputs = workflow_run_inputs(
            {
                "clarificationAnswers": {
                    "unit_test_confirmation": {
                        "selected": "skip",
                    }
                },
                "resumeState": {
                    "summary": {
                        "status": "requires_user_input",
                        "phase": "unit_test",
                    }
                },
            }
        )

        self.assertEqual(inputs["resume_from"], "unit_test")
        self.assertEqual(inputs["resume_values"]["unit_test_decision"], "skip")
        self.assertNotIn("user_interaction_submission", inputs)

    def test_test_phase_confirmation_is_forwarded_as_structured_resume(self) -> None:
        """进入测试阶段按钮必须恢复确认节点并保留结构化动作。"""

        inputs = workflow_run_inputs(
            {
                "request": "开始测试页面：请假申请页",
                "clarificationAnswers": {
                    "test_phase_confirmation": {
                        "action": "confirm",
                    }
                },
                "resumeState": {
                    "summary": {
                        "status": "requires_user_input",
                        "phase": "test_phase_confirmation",
                    }
                },
            }
        )

        self.assertEqual(inputs["resume_from"], "test_phase_confirmation")
        self.assertEqual(
            inputs["resume_values"]["test_phase_confirmation"],
            {"mode": "test_phase_confirmation", "action": "confirm"},
        )

    def test_test_phase_confirmation_restores_build_code_diff_for_test_generation(
        self,
    ) -> None:
        """测试新对话必须恢复开发 Build Diff，供 TestGeneration Agent 生成单测。"""

        source_diff = (
            "@@ -1 +1,2 @@\n"
            "-public List<LeaveRecord> list() { return List.of(); }\n"
            "+public List<LeaveRecord> list() { return repository.findAll(); }\n"
        )
        code_changes = {
            "id": "code-change-set:build",
            "workspaceRoot": "/workspace",
            "workspaceName": "workspace",
            "files": [
                {
                    "id": "file:leave-service",
                    "path": "backend/src/main/java/demo/LeaveRecordService.java",
                    "changeType": "modified",
                    "additions": 1,
                    "deletions": 1,
                    "diff": source_diff,
                }
            ],
            "summary": {"files": 1, "additions": 1, "deletions": 1},
        }

        inputs = workflow_run_inputs(
            {
                "request": "开始测试接口：请假记录查询",
                "clarificationAnswers": {
                    "test_phase_confirmation": {"action": "confirm"}
                },
                "resumeState": {
                    "summary": {
                        "status": "requires_user_input",
                        "phase": "test_phase_confirmation",
                    },
                    "state": {
                        "phase": "test_phase_confirmation",
                        "codeChanges": code_changes,
                    },
                },
            }
        )

        self.assertEqual(inputs["resume_from"], "test_phase_confirmation")
        self.assertEqual(inputs["resume_values"]["code_changes"], code_changes)
        self.assertIn(
            "repository.findAll()",
            inputs["resume_values"]["code_changes"]["files"][0]["diff"],
        )
        collected = collect_unit_test_targets(
            {
                **inputs["resume_values"],
                "test_generation_input_code_changes": inputs["resume_values"][
                    "code_changes"
                ],
                "test_generation_input_code_change_sets": [],
            }
        )
        generation_context = collected["unit_test_generation_context"]
        self.assertEqual(
            generation_context["source_files"],
            ["backend/src/main/java/demo/LeaveRecordService.java"],
        )
        self.assertIn(
            "repository.findAll()",
            generation_context["code_diff"]["files"][0]["diff"],
        )
        self.assertIn("repository.findAll()", _build_prompt(generation_context))

    def test_test_phase_confirmation_rejects_non_confirm_action(self) -> None:
        """测试阶段确认不接受拒绝动作或自然语言回退。"""

        with self.assertRaisesRegex(ValueError, "只支持 confirm"):
            workflow_run_inputs(
                {
                    "request": "进入测试",
                    "clarificationAnswers": {
                        "test_phase_confirmation": {"action": "reject"}
                    },
                }
            )

        with self.assertRaisesRegex(ValueError, "只能通过 clarificationAnswers"):
            workflow_run_inputs(
                {
                    "request": "进入审查",
                    "resumeState": {
                        "summary": {
                            "status": "requires_user_input",
                            "phase": "review_phase_confirmation",
                        }
                    },
                }
            )

    def test_review_phase_confirmation_is_forwarded_as_structured_resume(self) -> None:
        """进入审查阶段按钮必须恢复确认节点并保留 confirm 动作。"""

        inputs = workflow_run_inputs(
            {
                "request": "开始审查前后端代码",
                "clarificationAnswers": {
                    "review_phase_confirmation": {"action": "confirm"}
                },
                "resumeState": {
                    "summary": {
                        "status": "requires_user_input",
                        "phase": "review_phase_confirmation",
                    }
                },
            }
        )

        self.assertEqual(inputs["resume_from"], "review_phase_confirmation")
        self.assertEqual(
            inputs["resume_values"]["review_phase_confirmation"],
            {"mode": "review_phase_confirmation", "action": "confirm"},
        )

    def test_review_phase_confirmation_rejects_non_confirm_action(self) -> None:
        """审查阶段确认不接受未知动作或自然语言冒充确认。"""

        with self.assertRaisesRegex(ValueError, "只支持 confirm"):
            workflow_run_inputs(
                {
                    "request": "进入审查",
                    "clarificationAnswers": {
                        "review_phase_confirmation": {"action": "skip"}
                    },
                }
            )

    def test_code_review_repair_confirmation_is_forwarded_as_structured_resume(self) -> None:
        """一键修复按钮必须恢复同一代码审查节点并携带 repair_all 动作。"""

        inputs = workflow_run_inputs(
            {
                "request": "开始一键修复扫描出的代码问题",
                "clarificationAnswers": {
                    "code_review_repair_confirmation": {"action": "repair_all"}
                },
                "resumeState": {
                    "summary": {
                        "status": "requires_user_input",
                        "phase": "code_review",
                    },
                    "state": {
                        "clarification": {
                            "mode": "code_review_repair_confirmation",
                            "status": "requires_user_input",
                        },
                        "codeReviewResult": {
                            "status": "completed",
                            "issues": [{"id": "CKR6002-1"}],
                        },
                    },
                },
            }
        )

        self.assertEqual(inputs["resume_from"], "code_review")
        self.assertEqual(
            inputs["resume_values"]["code_review_repair_confirmation"],
            {"mode": "code_review_repair_confirmation", "action": "repair_all"},
        )
        self.assertEqual(inputs["resume_values"]["code_review_result"]["issues"][0]["id"], "CKR6002-1")

    def test_code_review_repair_confirmation_rejects_unknown_action_or_missing_answer(self) -> None:
        """代码审查恢复只接受结构化 repair_all，不允许自然语言或过期快照绕过。"""

        with self.assertRaisesRegex(ValueError, "只支持 repair_all"):
            workflow_run_inputs(
                {
                    "clarificationAnswers": {
                        "code_review_repair_confirmation": {"action": "skip"}
                    }
                }
            )

        with self.assertRaisesRegex(ValueError, "只能通过 clarificationAnswers"):
            workflow_run_inputs(
                {
                    "resumeState": {
                        "summary": {
                            "status": "requires_user_input",
                            "phase": "code_review",
                        },
                        "state": {
                            "clarification": {
                                "mode": "code_review_repair_confirmation"
                            },
                        },
                    }
                }
            )

    def test_frontend_performance_confirmation_is_forwarded_as_resume_decision(self) -> None:
        """前端性能测试确认按钮必须转换为主 Workflow 可消费的 skip/run 状态。"""

        inputs = workflow_run_inputs(
            {
                "clarificationAnswers": {
                    "frontend_performance_confirmation": {
                        "selected": "run",
                    }
                },
                "resumeState": {
                    "summary": {
                        "status": "requires_user_input",
                        "phase": "integration_test",
                    }
                },
            }
        )

        self.assertEqual(inputs["resume_from"], "integration_test")
        self.assertEqual(
            inputs["resume_values"]["frontend_performance_decision"],
            "run",
        )
        self.assertNotIn("user_interaction_submission", inputs)

    def test_performance_resume_keeps_integration_build_cache(self) -> None:
        """性能测试确认恢复时必须保留集成构建缓存，避免测试阶段从头重跑。"""

        inputs = workflow_run_inputs(
            {
                "clarificationAnswers": {
                    "frontend_performance_confirmation": {
                        "selected": "run",
                    }
                },
                "resumeState": {
                    "summary": {
                        "status": "requires_user_input",
                        "phase": "integration_test",
                    },
                    "result": {
                        "phase": "integration_test",
                        "status": "requires_user_input",
                        "clarification": {
                            "mode": "frontend_performance_confirmation"
                        },
                        "unit_test_decision": "skip",
                        "integration_build_checks_completed": True,
                        "integration_build_results": [
                            {"id": "frontend_build", "passed": True}
                        ],
                    }
                },
            }
        )

        self.assertEqual(inputs["resume_from"], "integration_test")
        self.assertEqual(inputs["resume_values"]["unit_test_decision"], "skip")
        self.assertTrue(inputs["resume_values"]["integration_build_checks_completed"])
        self.assertEqual(
            inputs["resume_values"]["integration_build_results"][0]["id"],
            "frontend_build",
        )
        self.assertEqual(
            inputs["resume_values"]["frontend_performance_decision"],
            "run",
        )

    def test_removed_requirements_resume_falls_back_to_main_start(self) -> None:
        inputs = workflow_run_inputs(
            {
                "request": "补充后的需求",
                "forwardedProps": {
                    "resumeState": {
                        "events": [
                            {
                                "type": "workflow.node.completed",
                                "nodeName": "requirements",
                                "status": "requires_user_input",
                            }
                        ]
                    }
                },
            }
        )

        self.assertEqual(inputs["resume_from"], "")

    def test_clarification_answers_default_to_development_readiness_resume(self) -> None:
        inputs = workflow_run_inputs(
            {
                "originalRequest": "帮我做一个库房系统",
                "clarificationAnswers": {"用户角色": ["库管员"]},
            }
        )

        self.assertEqual(inputs["resume_from"], "development_readiness_gate")
        self.assertNotIn("原始需求：\n请基于原始需求", inputs["request"])
        self.assertIn("回答：库管员", inputs["request"])

    def test_application_planning_accepts_only_current_resume_nodes(self) -> None:
        inputs = workflow_run_inputs(
            {
                "request": "正确，继续",
                "forwardedProps": {
                    "workflowScope": "application_planning",
                    "resumeState": {
                        "events": [
                            {
                                "nodeName": "technical_planning",
                                "status": "requires_user_input",
                            }
                        ]
                    },
                },
            }
        )

        self.assertEqual(inputs["resume_from"], "technical_planning")

    def test_explicit_debug_resume_node_overrides_resume_snapshot(self) -> None:
        """节点调试选择必须覆盖旧快照中的阻断节点。"""

        inputs = workflow_run_inputs(
            {
                "request": "从指定节点继续执行 workflow 调试。",
                "forwardedProps": {
                    "resumeState": {
                        "events": [
                            {
                                "nodeName": "build",
                                "status": "requires_user_input",
                            }
                        ]
                    },
                    "workflowDebug": {
                        "enabled": True,
                        "resumeFrom": "prepare_build_tasks",
                    },
                },
            }
        )

        self.assertEqual(inputs["resume_from"], "prepare_build_tasks")

    def test_debug_resume_exposes_flag_for_plan_adjustment_takeover(self) -> None:
        """节点调试请求应向生命周期层声明这是用户明确选择的恢复动作。"""

        inputs = workflow_run_inputs(
            {
                "workflowDebug": {
                    "enabled": True,
                    "resumeFrom": "prepare_build_tasks",
                }
            }
        )

        self.assertTrue(inputs["workflow_debug_enabled"])

    def test_acceptance_local_fix_routes_to_small_task_repair(self) -> None:
        inputs = workflow_run_inputs(
            {
                "clarificationAnswers": {
                    "page_acceptance": "changes_requested",
                    "acceptance_adjustment": {
                        "type": "local_fix",
                        "feedback": "把页面右上角的按钮间距调大。",
                    },
                }
            }
        )

        self.assertEqual(inputs["resume_from"], "small_task_repair")
        self.assertEqual(
            inputs["resume_values"]["acceptance_adjustment"],
            {
                "type": "local_fix",
                "feedback": "把页面右上角的按钮间距调大。",
            },
        )

    def test_acceptance_design_and_plan_changes_route_to_their_confirmation_nodes(self) -> None:
        for adjustment_type, expected_node in (
            ("page_design_change", "project_planning"),
            ("endpoint_change", "project_planning"),
            ("data_source_change", "entity_source_binding"),
            ("project_plan_change", "project_planning"),
        ):
            with self.subTest(adjustment_type=adjustment_type):
                inputs = workflow_run_inputs(
                    {
                        "clarificationAnswers": {
                            "page_acceptance": "changes_requested",
                            "acceptance_adjustment": {
                                "type": adjustment_type,
                                "feedback": "调整验收反馈对应的设计。",
                            },
                        }
                    }
                )
                self.assertEqual(inputs["resume_from"], expected_node)

    def test_acceptance_adjustment_rejects_unknown_types(self) -> None:
        with self.assertRaises(ValueError):
            workflow_run_inputs(
                {
                    "clarificationAnswers": {
                        "page_acceptance": "changes_requested",
                        "acceptance_adjustment": {
                            "type": "unknown",
                            "feedback": "不应被静默路由。",
                        },
                    }
                }
            )

    def test_debug_resume_ignores_empty_acceptance_adjustment_snapshot(self) -> None:
        """节点调试恢复不应把公开快照中的空验收调整误判为非法输入。"""

        inputs = workflow_run_inputs(
            {
                "forwardedProps": {
                    "workflowDebug": {
                        "enabled": True,
                        "resumeFrom": "prepare_build_tasks",
                    },
                    "resumeState": {
                        "runId": "previous-run",
                        "state": {
                            "acceptanceAdjustment": {},
                            "selectedPageId": "pet_list_page",
                            "buildExecutionScope": {
                                "type": "page",
                                "targetId": "pet_list_page",
                            },
                        },
                    },
                }
            }
        )

        self.assertEqual(inputs["resume_from"], "prepare_build_tasks")
        self.assertNotIn("acceptance_adjustment", inputs["resume_values"])
        self.assertEqual(
            inputs["resume_values"]["build_execution_scope"],
            {"type": "page", "targetId": "pet_list_page"},
        )

    def test_merges_other_choice_input_as_a_requirement_supplement(self) -> None:
        inputs = workflow_run_inputs(
            {
                "originalRequest": "创建库存管理系统",
                "clarificationAnswers": {
                    "库存页面": {
                        "selected": ["库存列表"],
                        "other": "列表必须支持按仓库分组并导出 Excel",
                    }
                },
            }
        )

        self.assertIn("已选：库存列表", inputs["request"])
        self.assertIn("其他补充：列表必须支持按仓库分组并导出 Excel", inputs["request"])
        self.assertNotIn("__other__", inputs["request"])

    def test_preserves_requirement_spec_from_state_snapshot_resume(self) -> None:
        inputs = workflow_run_inputs(
            {
                "request": "补充角色和页面信息",
                "forwardedProps": {
                    "resumeState": {
                        "state": {
                            "status": "requires_user_input",
                            "phase": "requirements",
                            "requirement_spec": {
                                "confirmation_status": "pending_user_input",
                                "clarification_status": "requires_user_input",
                            },
                            "requirement_spec_path": "var/specs/requirement-spec.md",
                            "requirement_spec_json_path": "var/specs/requirement-spec.json",
                        }
                    }
                },
            }
        )

        self.assertEqual(inputs["resume_from"], "")
        self.assertEqual(
            inputs["resume_values"]["requirement_spec"]["confirmation_status"],
            "pending_user_input",
        )
        self.assertEqual(
            inputs["resume_values"]["requirement_spec_path"],
            "var/specs/requirement-spec.md",
        )

    def test_extracts_structured_entity_source_binding_submission(self) -> None:
        submission = {
            "review_status": "confirmed",
            "target_changes": [
                {
                    "target_type": "entity",
                    "target_id": "Inventory",
                    "changes": {"risks": []},
                }
            ],
        }
        inputs = workflow_run_inputs(
            {
                "clarificationAnswers": {"entity_source_binding": submission},
                "forwardedProps": {
                    "resumeState": {
                        "events": [
                            {
                                "type": "workflow.node.completed",
                                "node": {"id": "entity_source_binding"},
                                "status": "requires_user_input",
                            }
                        ],
                        "result": {
                            "pending_project_plan": {"confirmation_status": "pending_user_confirmation"}
                        },
                    }
                },
            }
        )

        self.assertEqual(inputs["resume_from"], "entity_source_binding")
        self.assertEqual(
            inputs["resume_values"]["entity_source_binding_submission"],
            submission,
        )

    def test_extracts_entity_table_selection_action_with_resume_state(self) -> None:
        """AI 智能选表请求应同时恢复实体节点、结构化动作和交互令牌。"""

        action = {
            "action": "ai_assist",
            "entity_id": "entity_category",
            "assist_type": "table_selection",
            "context": {
                "fields": [{"name": "id", "type": "text", "required": True}],
                "available_tables": [{"name": "category", "comment": "商品分类"}],
            },
        }
        inputs = workflow_run_inputs(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": "已请求 AI 辅助，请查看表单内的建议并采纳。",
                    }
                ],
                "forwardedProps": {
                    "clarificationAnswers": {"entity_design": action},
                    "selectedEntityId": "entity_category",
                    "detailTargetType": "entity",
                    "resumeState": {
                        "runId": "run-entity-binding",
                        "state": {
                            "selected_entity_id": "entity_category",
                            "pending_project_plan": {
                                "confirmation_status": "pending_user_confirmation"
                            },
                            "lifecycle": {
                                "activeExecutions": {
                                    "run-entity-binding": {
                                        "pendingInteraction": {
                                            "id": "interaction-entity-binding",
                                            "basedOnRevision": 8,
                                        }
                                    }
                                }
                            },
                        },
                        "summary": {
                            "phase": "entity_source_binding",
                            "status": "requires_user_input",
                        },
                    },
                },
            }
        )

        self.assertEqual(inputs["resume_from"], "entity_source_binding")
        self.assertEqual(inputs["resume_values"]["entity_design_action"], action)
        self.assertEqual(
            inputs["resume_values"]["selected_entity_id"],
            "entity_category",
        )
        self.assertEqual(
            inputs["resume_values"]["lifecycle_interaction_submission"],
            {
                "id": "interaction-entity-binding",
                "basedOnRevision": 8,
                "runId": "run-entity-binding",
            },
        )

    def test_project_planning_resume_preserves_plan_state(self) -> None:
        inputs = workflow_run_inputs(
            {
                "request": "正确，继续",
                "forwardedProps": {
                    "resumeState": {
                        "events": [
                            {
                                "type": "workflow.node.completed",
                                "node": {"id": "project_planning"},
                                "status": "requires_user_input",
                            }
                        ],
                        "result": {
                            "requirement_spec": {"version": "0.1.0"},
                            "project_plan": {"version": "0.1.0"},
                        },
                    }
                },
            }
        )

        self.assertEqual(inputs["resume_from"], "project_planning")
        self.assertEqual(inputs["resume_values"]["project_plan"], {"version": "0.1.0"})
        self.assertEqual(
            inputs["resume_values"]["requirement_spec"],
            {"version": "0.1.0"},
        )

    def test_does_not_load_project_plan_as_technical_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            plans_dir = workspace / "plans"
            plans_dir.mkdir()
            project_plan = {
                "version": "0.1.0",
                "frontend_pages": [
                    {
                        "id": "inventory_page",
                        "name": "库存页面",
                        "description": "查看、筛选和导出库存。",
                    }
                ],
            }
            (plans_dir / "project-plan.json").write_text(
                json.dumps(project_plan, ensure_ascii=False),
                encoding="utf-8",
            )

            inputs = workflow_run_inputs(
                {
                    "request": "开始开发",
                    "forwardedProps": {"workspaceRoot": str(workspace)},
                }
            )

        self.assertNotIn("project_plan", inputs.get("resume_values", {}))
        self.assertNotIn("frontend_pages", inputs.get("resume_values", {}))

    def test_materializes_compact_technical_plan_for_main_workflow(self) -> None:
        """主 Workflow 应按需合并当前上游产物，而不改写正式 TechnicalPlan。"""

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            plans_dir = workspace / ".xcodeagent" / "plans"
            specs_dir = workspace / ".xcodeagent" / "specs"
            plans_dir.mkdir(parents=True)
            specs_dir.mkdir(parents=True)
            technical_plan = {
                "artifact_type": "technical-plan",
                "confirmation_status": "confirmed",
                "architecture": {},
                "engineering_design": {},
                "api_contracts": [],
                "pages": [
                    {
                        "pageId": "inventory_page",
                        "references": {
                            "endpoint_dependencies": [],
                            "action_implementations": [],
                        },
                    }
                ],
            }
            requirement_spec = {
                "confirmation_status": "confirmed",
                "app_info": {"name": "库存应用", "summary": "管理库存"},
                "user_roles": [],
                "feature_modules": [],
                "business_flows": [],
                "acceptance_criteria": [],
                "data_sources": [],
                "pages": [{"pageId": "inventory_page"}],
            }
            product_plan = {
                "schema_version": "product-plan.v5",
                "confirmation_status": "confirmed",
                "app": {"name": "库存应用", "summary": "管理库存"},
                "business_flows": [],
                "product_acceptance_criteria": [],
                "pages": [
                    {
                        "pageId": "inventory_page",
                        "name": "库存页面",
                        "path": "/inventory",
                        "module_id": "inventory",
                        "description": "管理库存",
                        "actions": [],
                        "allowed_roles": [],
                        "navigation_targets": [],
                        "acceptance_criteria": [],
                    }
                ],
            }
            ui_designs = {
                "schema_version": "ui-manifest.v3",
                "confirmation_status": "confirmed",
                "pages": [
                    {
                        "pageId": "inventory_page",
                        "code_path": ".xcodeagent/ui-design/pages/Inventory/index.tsx",
                        "code_sha256": "a" * 64,
                    }
                ],
            }
            for path, value in (
                (plans_dir / "technical-plan.json", technical_plan),
                (plans_dir / "product-plan.json", product_plan),
                (specs_dir / "requirement-spec.json", requirement_spec),
                (specs_dir / "ui-designs.json", ui_designs),
            ):
                path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

            inputs = workflow_run_inputs(
                {
                    "request": "开始开发",
                    "forwardedProps": {"workspaceRoot": str(workspace)},
                }
            )

        self.assertNotIn(
            "page_implementation_contracts",
            inputs["resume_values"]["technical_plan"],
        )
        self.assertEqual(
            inputs["resume_values"]["project_plan"]["pages"][0]["name"],
            "库存页面",
        )
        self.assertEqual(
            inputs["resume_values"]["project_plan"]["page_implementation_contracts"][0]["pageId"],
            "inventory_page",
        )

    def test_selected_requirement_page_does_not_bypass_technical_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            plans_dir = workspace / ".xcodeagent" / "plans"
            specs_dir = workspace / ".xcodeagent" / "specs"
            plans_dir.mkdir(parents=True)
            specs_dir.mkdir(parents=True)
            (plans_dir / "project-plan.json").write_text(
                json.dumps({"frontend_pages": []}, ensure_ascii=False),
                encoding="utf-8",
            )
            requirement_page = {
                "id": "inventory_page",
                "name": "库存页面",
                "path": "/inventory",
                "module_id": "inventory",
                "description": "查看和筛选库存。",
            }
            (specs_dir / "requirement-spec.json").write_text(
                json.dumps({"pages": [requirement_page]}, ensure_ascii=False),
                encoding="utf-8",
            )

            inputs = workflow_run_inputs(
                {
                    "request": "开始设计库存页面",
                    "forwardedProps": {
                        "workspaceRoot": str(workspace),
                        "selectedPageId": "inventory_page",
                    },
                }
            )

        self.assertNotIn("project_plan", inputs.get("resume_values", {}))
        self.assertNotIn("frontend_pages", inputs.get("resume_values", {}))

    def test_forwards_selected_page_id_to_development_readiness_state(self) -> None:
        inputs = workflow_run_inputs(
            {
                "request": "开始设计库存页面",
                "forwardedProps": {"selectedPageId": "inventory_page"},
            }
        )

        self.assertEqual(
            inputs["resume_values"]["selectedPageId"],
            "inventory_page",
        )

    def test_restores_selected_page_scope_from_resume_state(self) -> None:
        """开发前置检查暂停后仍恢复页面构建范围。"""

        inputs = workflow_run_inputs(
            {
                "request": "正确，继续",
                "forwardedProps": {
                    "resumeState": {
                        "state": {
                            "phase": "development_readiness_gate",
                            "status": "requires_user_input",
                            "selectedPageId": "page_1",
                        }
                    }
                },
            }
        )

        self.assertEqual(inputs["resume_from"], "development_readiness_gate")
        self.assertEqual(inputs["resume_values"]["selectedPageId"], "page_1")
        self.assertEqual(
            inputs["resume_values"]["build_execution_scope"],
            {"type": "page", "targetId": "page_1"},
        )

    def test_build_gate_failure_retry_returns_to_task_generation(self) -> None:
        """旧 DAG 的 Build 门禁失败必须重新生成当前范围，不能原地重复 Build。"""

        inputs = workflow_run_inputs(
            {
                "request": "重试当前计划任务。",
                "forwardedProps": {
                    "workflowAction": "retry_failed_tasks",
                    "selectedPageId": "customers",
                    "resumeState": {
                        "events": [
                            {
                                "status": "failed",
                                "nodeName": "build",
                            }
                        ],
                        "state": {
                            "buildSummary": {
                                "status": "failed",
                                "gate_errors": [
                                    "Build DAG scope 与当前 Build scope 不一致。"
                                ],
                            },
                            "buildExecutionScope": {
                                "type": "page",
                                "targetId": "customers",
                            },
                        },
                    },
                },
            }
        )

        self.assertEqual(inputs["resume_from"], "prepare_build_tasks")
        self.assertFalse(inputs["resume_values"]["retry_failed_tasks"])

    def test_scope_mismatch_retry_recovers_without_gate_error_projection(self) -> None:
        """历史快照缺少 gate_errors 时也应根据计划范围不一致恢复 DAG 生成。"""

        node = _retry_failed_execution_node(
            None,
            {
                "build_execution_scope": {"type": "page", "targetId": "customers"},
                "build_task_plan": {
                    "build_execution_scope": {"type": "page", "targetId": "orders"},
                },
            },
        )

        self.assertEqual(node, "prepare_build_tasks")

    def test_forwards_explicit_build_execution_scope(self) -> None:
        """AG-UI 请求应把页面/数据源范围作为 Workflow State 的结构化输入。"""

        inputs = workflow_run_inputs(
            {
                "request": "生成订单数据源",
                "forwardedProps": {
                    "buildExecutionScope": {"type": "data_source", "targetId": "orders"},
                },
            }
        )

        self.assertEqual(
            inputs["resume_values"]["build_execution_scope"],
            {"type": "data_source", "targetId": "orders"},
        )

    def test_selected_page_overrides_stale_resumed_application_scope(self) -> None:
        """恢复态存在旧 application 范围时，选中页面应纠正为页面范围。"""

        inputs = workflow_run_inputs(
            {
                "request": "正确，继续",
                "forwardedProps": {
                    "resumeState": {
                        "state": {
                            "phase": "development_readiness_gate",
                            "status": "requires_user_input",
                            "selectedPageId": "page_1",
                            "build_execution_scope": {
                                "type": "application",
                                "targetId": "application",
                            },
                        }
                    }
                },
            }
        )

        self.assertEqual(
            inputs["resume_values"]["build_execution_scope"],
            {"type": "page", "targetId": "page_1"},
        )

    def test_selected_page_overrides_stale_resumed_page_scope(self) -> None:
        """用户重新选择页面后，应以本次选择覆盖恢复态中的旧页面范围。"""

        inputs = workflow_run_inputs(
            {
                "request": "正确，继续",
                "forwardedProps": {
                    "selectedPageId": "personnel-list",
                    "resumeState": {
                        "state": {
                            "phase": "development_readiness_gate",
                            "status": "requires_user_input",
                            "selectedPageId": "page_1",
                            "build_execution_scope": {
                                "type": "page",
                                "targetId": "page_1",
                            },
                        }
                    },
                },
            }
        )

        self.assertEqual(inputs["resume_values"]["selectedPageId"], "personnel-list")
        self.assertEqual(
            inputs["resume_values"]["build_execution_scope"],
            {"type": "page", "targetId": "personnel-list"},
        )

    def test_selected_page_does_not_read_project_plan_fallback(self) -> None:
        """只存在 project-plan.json 时，不应把它当作当前 TechnicalPlan。"""

        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            plans_dir = workspace / ".xcodeagent" / "plans"
            plans_dir.mkdir(parents=True)
            (plans_dir / "project-plan.json").write_text(
                json.dumps(
                    {
                        "confirmation_status": "confirmed",
                        "frontend_pages": [
                            {
                                "pageId": "page-personnel-list",
                                "name": "人员列表",
                                "path": "/",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            inputs = workflow_run_inputs(
                {
                    "request": "正确，继续",
                    "forwardedProps": {
                        "workspaceRoot": str(workspace),
                        "selectedPageId": "personnel-list",
                        "resumeState": {
                            "state": {
                                "phase": "development_readiness_gate",
                                "status": "requires_user_input",
                                "selectedPageId": "page_1",
                                "build_execution_scope": {
                                    "type": "page",
                                    "targetId": "page_1",
                                },
                            }
                        },
                    },
                }
            )

        self.assertNotIn("project_plan", inputs.get("resume_values", {}))
        self.assertNotIn("frontend_pages", inputs.get("resume_values", {}))

    def test_infers_prepare_build_tasks_resume_for_plan_confirmation_guard(self) -> None:
        inputs = workflow_run_inputs(
            {
                "request": "正确，继续",
                "forwardedProps": {
                    "resumeState": {
                        "events": [
                            {
                                "type": "workflow.node.completed",
                                "node": {"id": "prepare_build_tasks"},
                                "status": "requires_user_input",
                            }
                        ],
                        "result": {
                            "project_plan": {
                                "version": "0.1.0",
                                "confirmation_status": "pending_user_confirmation",
                            },
                        },
                    }
                },
            }
        )

        self.assertEqual(inputs["resume_from"], "prepare_build_tasks")
        self.assertEqual(
            inputs["resume_values"]["project_plan"]["confirmation_status"],
            "pending_user_confirmation",
        )

    def test_preserves_workspace_snapshot_refs_from_resume_state(self) -> None:
        inputs = workflow_run_inputs(
            {
                "request": "从任务拆分继续",
                "forwardedProps": {
                    "resumeState": {
                        "state": {
                            "workspace_snapshot_summary": {
                                "workspace_revision": "rev-123"
                            },
                            "workspace_snapshot_path": "/tmp/snapshot.json",
                            "workspace_snapshot_hash": "hash-123",
                            "workspace_revision": "rev-123",
                        }
                    }
                },
            }
        )

        self.assertEqual(
            inputs["resume_values"]["workspace_snapshot_path"],
            "/tmp/snapshot.json",
        )
        self.assertEqual(
            inputs["resume_values"]["workspace_snapshot_summary"]["workspace_revision"],
            "rev-123",
        )

    def test_loads_workspace_snapshot_from_debug_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            snapshot_path = Path(tmpdir) / "snapshot.json"
            snapshot_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "workspace_revision": "rev-debug",
                        "tech_stack": ["FastAPI", "React"],
                        "entrypoints": [{"path": "Backend/app/main.py"}],
                        "project_roots": [{"path": "Backend/app"}],
                        "file_manifest": {"total_files_indexed": 12},
                        "code_graph": {"provider": "none", "available": False},
                    }
                ),
                encoding="utf-8",
            )

            inputs = workflow_run_inputs(
                {
                    "workflowDebug": {
                        "resume_from": "inspect_workspace",
                        "workspace_snapshot_path": str(snapshot_path),
                    }
                }
            )

        self.assertEqual(inputs["resume_from"], "inspect_workspace")
        self.assertEqual(inputs["resume_values"]["workspace_revision"], "rev-debug")
        self.assertEqual(
            inputs["resume_values"]["workspace_snapshot_summary"]["tech_stack"],
            ["FastAPI", "React"],
        )

    def test_legacy_database_context_resume_maps_to_task_preparation(self) -> None:
        """旧快照节点兼容重定向，并丢弃已退役的数据库规划状态。"""

        inputs = workflow_run_inputs(
            {
                "resumeState": {
                    "events": [
                        {
                            "status": "requires_user_input",
                            "nodeName": "inspect_database_context",
                        }
                    ],
                    "state": {
                        "database_planning_context": {"status": "completed"},
                    },
                }
            }
        )

        self.assertEqual(inputs["resume_from"], "prepare_build_tasks")
        self.assertNotIn("database_planning_context", inputs["resume_values"])

    def test_workflow_debug_auto_loads_fixed_workspace_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            specs_dir = workspace / ".xcodeagent" / "specs"
            plans_dir = workspace / ".xcodeagent" / "plans"
            snapshots_dir = workspace / ".xcodeagent" / "cache" / "workspace-snapshots"
            specs_dir.mkdir(parents=True)
            plans_dir.mkdir(parents=True)
            snapshots_dir.mkdir(parents=True)
            (specs_dir / "requirement-spec.json").write_text(
                json.dumps({"version": "spec-v1"}),
                encoding="utf-8",
            )
            (plans_dir / "project-plan.json").write_text(
                json.dumps({"version": "plan-v1"}),
                encoding="utf-8",
            )
            (plans_dir / "build-task-plan.json").write_text(
                json.dumps(
                    {
                        "schema_version": "build-dag.v3",
                        "task_registry": {"task-1": {"id": "task-1"}},
                        "task_graph": {"nodes": ["task-1"], "edges": []},
                    }
                ),
                encoding="utf-8",
            )
            (snapshots_dir / "rev.1.0.0.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "workspace_revision": "rev-auto",
                        "tech_stack": ["React"],
                        "entrypoints": [],
                        "project_roots": [],
                        "file_manifest": {},
                        "code_graph": {"provider": "none", "available": False},
                    }
                ),
                encoding="utf-8",
            )

            inputs = workflow_run_inputs(
                {
                    "forwardedProps": {
                        "workspaceRoot": str(workspace),
                        "workflowDebug": {"enabled": True, "resumeFrom": "build"},
                    }
                }
            )

        self.assertEqual(inputs["resume_from"], "build")
        self.assertEqual(inputs["resume_values"]["requirement_spec"]["version"], "spec-v1")
        self.assertEqual(inputs["resume_values"]["project_plan"]["version"], "plan-v1")
        self.assertEqual(inputs["resume_values"]["tasks"], [{"id": "task-1"}])
        self.assertEqual(inputs["resume_values"]["workspace_revision"], "rev-auto")

    def test_integration_test_debug_starts_with_fresh_repair_budget(self) -> None:
        """验证显式集成测试调试不会继承同一会话已耗尽的修复计数。"""

        inputs = workflow_run_inputs(
            {
                "workflowDebug": {
                    "enabled": True,
                    "resumeFrom": "integration_test",
                }
            }
        )

        self.assertEqual(inputs["resume_from"], "integration_test")
        self.assertEqual(inputs["resume_values"]["repair_iteration"], 0)
        self.assertEqual(inputs["resume_values"]["max_repair_iterations"], 3)
        self.assertEqual(inputs["resume_values"]["repair_task_plan"], {})
        self.assertEqual(inputs["resume_values"]["repair_tasks"], [])

    def test_retry_failed_tasks_is_an_explicit_build_action(self) -> None:
        """显式重试动作必须固定从 Build 恢复，并写入受控 Graph State。"""

        inputs = workflow_run_inputs(
            {
                "forwardedProps": {
                    "workflowAction": "retry_failed_tasks",
                    "resumeFrom": "integration_test",
                }
            }
        )

        self.assertEqual(inputs["workflow_action"], "retry_failed_tasks")
        self.assertEqual(inputs["resume_from"], "build")
        self.assertTrue(inputs["resume_values"]["retry_failed_tasks"])

    def test_retry_restores_camel_case_repair_plan_from_public_state_snapshot(self) -> None:
        """公开 StateSnapshot 的 camelCase 修复计划必须能回到 Build 恢复输入。"""

        repair_plan = {
            "status": "ready",
            "decision": "repair",
            "tasks": [{"id": "repair-page", "status": "pending"}],
        }
        inputs = workflow_run_inputs(
            {
                "forwardedProps": {
                    "workflowAction": "retry_failed_tasks",
                    "resumeState": {
                        "state": {
                            "buildSummary": {"repairable_failures": 1},
                            "repairTaskPlan": repair_plan,
                        }
                    },
                }
            }
        )

        self.assertEqual(inputs["resume_from"], "build")
        self.assertEqual(inputs["resume_values"]["repair_task_plan"], repair_plan)
        self.assertEqual(inputs["resume_values"]["build_summary"], {"repairable_failures": 1})

    def test_retry_loads_persisted_repair_plan_when_public_snapshot_is_incomplete(self) -> None:
        """公开失败快照缺少修复计划时，重试必须恢复工作区中的 ready 计划。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            plans_dir = workspace / ".xcodeagent" / "plans"
            plans_dir.mkdir(parents=True)
            repair_plan = {
                "status": "ready",
                "decision": "repair",
                "tasks": [{"id": "repair-page", "status": "pending"}],
            }
            (plans_dir / "repair-task-plan.json").write_text(
                json.dumps(repair_plan),
                encoding="utf-8",
            )

            inputs = workflow_run_inputs(
                {
                    "workspace": str(workspace),
                    "forwardedProps": {
                        "workflowAction": "retry_failed_tasks",
                        "resumeState": {
                            "state": {
                                "buildSummary": {
                                    "repairable_failures": 1,
                                    "retry_available": False,
                                }
                            }
                        },
                    },
                }
            )

        self.assertEqual(inputs["resume_values"]["repair_task_plan"], repair_plan)
        self.assertEqual(
            inputs["resume_values"]["repair_task_plan_path"],
            str(workspace / ".xcodeagent" / "plans" / "repair-task-plan.json"),
        )
        self.assertEqual(inputs["resume_values"]["repair_tasks"], repair_plan["tasks"])


if __name__ == "__main__":
    unittest.main()
