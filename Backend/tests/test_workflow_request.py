from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.protocols.workflow.request import workflow_run_inputs


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

    def test_clarification_answers_default_to_detail_confirmation_resume(self) -> None:
        inputs = workflow_run_inputs(
            {
                "originalRequest": "帮我做一个库房系统",
                "clarificationAnswers": {"用户角色": ["库管员"]},
            }
        )

        self.assertEqual(inputs["resume_from"], "detail_confirmation")
        self.assertNotIn("原始需求：\n请基于原始需求", inputs["request"])
        self.assertIn("回答：库管员", inputs["request"])

    def test_application_planning_keeps_its_two_resume_nodes(self) -> None:
        inputs = workflow_run_inputs(
            {
                "request": "正确，继续",
                "forwardedProps": {
                    "workflowScope": "application_planning",
                    "resumeState": {
                        "events": [
                            {
                                "nodeName": "project_planning",
                                "status": "requires_user_input",
                            }
                        ]
                    },
                },
            }
        )

        self.assertEqual(inputs["resume_from"], "project_planning")

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

    def test_infers_detail_confirmation_resume_and_preserves_plan_state(self) -> None:
        inputs = workflow_run_inputs(
            {
                "request": "我选择 页面：库存管理列表页",
                "forwardedProps": {
                    "resumeState": {
                        "events": [
                            {
                                "type": "workflow.node.completed",
                                "node": {"id": "detail_confirmation"},
                                "status": "requires_user_input",
                            }
                        ],
                        "result": {
                            "project_plan": {"frontend_pages": []},
                            "project_plan_path": "var/plans/project-plan.md",
                            "page_spec_draft": {"pageId": "inventory_page"},
                        },
                    }
                },
            }
        )

        self.assertEqual(inputs["resume_from"], "detail_confirmation")
        self.assertEqual(inputs["resume_values"]["project_plan"], {"frontend_pages": []})
        self.assertEqual(
            inputs["resume_values"]["page_spec_draft"],
            {"pageId": "inventory_page"},
        )

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

    def test_extracts_structured_batch_detail_review_submission(self) -> None:
        submission = {
            "review_status": "confirmed",
            "target_changes": [
                {
                    "target_type": "page",
                    "target_id": "inventory_page",
                    "changes": {"interactions": ["搜索", "导出"]},
                }
            ],
        }
        inputs = workflow_run_inputs(
            {
                "clarificationAnswers": {"detail_review": submission},
                "forwardedProps": {
                    "resumeState": {
                        "events": [
                            {
                                "type": "workflow.node.completed",
                                "node": {"id": "detail_confirmation"},
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

        self.assertEqual(inputs["resume_from"], "detail_confirmation")
        self.assertEqual(
            inputs["resume_values"]["detail_review_submission"],
            submission,
        )

    def test_removed_project_planning_resume_preserves_plan_state(self) -> None:
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

        self.assertEqual(inputs["resume_from"], "")
        self.assertEqual(inputs["resume_values"]["project_plan"], {"version": "0.1.0"})
        self.assertEqual(
            inputs["resume_values"]["requirement_spec"],
            {"version": "0.1.0"},
        )

    def test_loads_project_plan_and_frontend_pages_for_normal_start(self) -> None:
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

        self.assertEqual(inputs["resume_values"]["project_plan"], project_plan)
        self.assertEqual(
            inputs["resume_values"]["frontend_pages"],
            project_plan["frontend_pages"],
        )

    def test_selected_requirement_page_is_added_for_detail_design(self) -> None:
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

        self.assertEqual(
            inputs["resume_values"]["frontend_pages"],
            [requirement_page],
        )
        self.assertEqual(
            inputs["resume_values"]["project_plan"]["frontend_pages"],
            [requirement_page],
        )

    def test_forwards_selected_page_id_to_detail_confirmation_state(self) -> None:
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
        """确认 PageDetail 后只携带恢复态时仍应恢复页面构建范围。"""

        inputs = workflow_run_inputs(
            {
                "request": "正确，继续",
                "forwardedProps": {
                    "resumeState": {
                        "state": {
                            "phase": "detail_confirmation",
                            "status": "requires_user_input",
                            "selectedPageId": "page_1",
                        }
                    }
                },
            }
        )

        self.assertEqual(inputs["resume_from"], "detail_confirmation")
        self.assertEqual(inputs["resume_values"]["selectedPageId"], "page_1")
        self.assertEqual(
            inputs["resume_values"]["build_execution_scope"],
            {"type": "page", "targetId": "page_1"},
        )

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
                            "phase": "detail_confirmation",
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
                            "phase": "detail_confirmation",
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

    def test_selected_page_uses_canonical_project_plan_page_id(self) -> None:
        """最新 ProjectPlan 有正式 pageId 时，应纠正旧页面别名并生成页面范围。"""

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
                                "phase": "detail_confirmation",
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

        self.assertEqual(inputs["resume_values"]["selectedPageId"], "page-personnel-list")
        self.assertEqual(
            inputs["resume_values"]["build_execution_scope"],
            {"type": "page", "targetId": "page-personnel-list"},
        )

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


if __name__ == "__main__":
    unittest.main()
