from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.protocols.workflow.request import workflow_run_inputs


class WorkflowRequestTests(unittest.TestCase):
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
                            "page_spec_draft": {"page_id": "inventory_page"},
                        },
                    }
                },
            }
        )

        self.assertEqual(inputs["resume_from"], "detail_confirmation")
        self.assertEqual(inputs["resume_values"]["project_plan"], {"frontend_pages": []})
        self.assertEqual(
            inputs["resume_values"]["page_spec_draft"],
            {"page_id": "inventory_page"},
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

    def test_forwards_selected_page_id_to_detail_confirmation_state(self) -> None:
        inputs = workflow_run_inputs(
            {
                "request": "开始设计库存页面",
                "forwardedProps": {"selectedPageId": "inventory_page"},
            }
        )

        self.assertEqual(
            inputs["resume_values"]["selected_page_id"],
            "inventory_page",
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
                json.dumps({"tasks": [{"id": "task-1"}]}),
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


if __name__ == "__main__":
    unittest.main()
