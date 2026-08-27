from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.graph.subgraphs.code_review import (
    MAX_CODE_REVIEW_REPAIR_ITERATIONS,
    _route_review_start,
    _public_build_checks,
    build_code_review_subgraph,
    code_review_repair,
    review_build_checks,
)
from app.workspace.code_changes import CapturedWorkspaceChanges


def _scan_result(*, issues: list[dict] | None = None) -> dict:
    """构造测试用的已归一化代码审查快照。"""

    normalized_issues = issues or []
    return {
        "status": "completed",
        "summary": "代码审查完成。",
        "issue_count": len(normalized_issues),
        "truncated": False,
        "loaded_skills": ["frontend-code-scan", "backend-code-scan"],
        "targets": [
            {
                "side": "frontend",
                "root": "frontend",
                "status": "completed",
                "scanned_file_count": 0,
                "warning": None,
            },
            {
                "side": "backend",
                "root": "backend/src/main/java",
                "status": "completed",
                "scanned_file_count": 1,
            },
        ],
        "issues": normalized_issues,
    }


def _issue() -> dict:
    """构造一个位于后端源码根目录内的问题。"""

    return {
        "id": "CKR6002-1",
        "side": "backend",
        "rule_id": "CKR6002",
        "severity": "high",
        "title": "连接未配置超时",
        "summary": "调用连接 API 前需要设置超时。",
        "file": "backend/src/main/java/PersonController.java",
        "line": 69,
    }


def _frontend_dependency_issue() -> dict:
    """构造一个要求修复 Agent 执行 pnpm install 的前端依赖问题。"""

    return {
        "id": "axios-version-risk-1",
        "side": "frontend",
        "rule_id": "axios-version-risk",
        "severity": "high",
        "title": "axios 版本风险",
        "summary": "按前端 Skill 更新依赖并执行 pnpm install。",
        "file": "frontend/package.json",
        "line": 12,
        "repair_actions": ["pnpm_install"],
    }


class CodeReviewSubgraphTests(unittest.TestCase):
    """验证代码审查子图的扫描、恢复和构建回环边界。"""

    def test_first_entry_scans_and_pauses_with_issues(self) -> None:
        """首次进入必须扫描一次，并在发现问题时等待结构化修复确认。"""

        with tempfile.TemporaryDirectory() as workspace, patch(
            "app.graph.subgraphs.code_review.analyze_workspace_code",
            return_value=_scan_result(issues=[_issue()]),
        ) as analyze:
            result = build_code_review_subgraph().invoke(
                {"workspace": workspace, "status": "in_progress"}
            )
            report_path = Path(result["code_review_report_path"])
            self.assertTrue(report_path.is_file())
            self.assertEqual(
                report_path.resolve().relative_to(Path(workspace).resolve()).as_posix(),
                ".xcodeagent/reports/code-review.md",
            )

        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(result["code_review_repair_status"], "awaiting_user")
        self.assertEqual(result["clarification"]["mode"], "code_review_repair_confirmation")
        analyze.assert_called_once()

    def test_repair_resume_does_not_scan_again(self) -> None:
        """携带有效问题快照恢复 repair_all 时不得再次调用扫描 Agent。"""

        issue = _issue()
        scan = _scan_result(issues=[issue])
        captured = CapturedWorkspaceChanges(
            value=json.dumps(
                {
                    "status": "completed",
                    "summary": "已修复连接超时配置。",
                    "attempted_issue_ids": [issue["id"]],
                    "changed_files": [issue["file"]],
                    "failure_reason": None,
                },
                ensure_ascii=False,
            ),
            code_change_set={
                "id": "code-change-set:test",
                "status": "applied",
                "files": [{"path": issue["file"], "changeType": "modified"}],
            },
        )
        with tempfile.TemporaryDirectory() as workspace, patch(
            "app.graph.subgraphs.code_review.analyze_workspace_code",
            side_effect=AssertionError("resume must not rescan"),
        ) as analyze, patch(
            "app.graph.subgraphs.code_review.capture_agent_file_changes",
            return_value=captured,
        ), patch(
            "app.graph.subgraphs.code_review.run_integration_checks",
            return_value={
                "test_results": [
                    {
                        "id": "frontend_install",
                        "name": "前端依赖安装检查",
                        "layer": "frontend",
                        "passed": True,
                        "required": True,
                    },
                    {
                        "id": "frontend_build",
                        "name": "前端构建检查",
                        "layer": "frontend",
                        "passed": True,
                        "required": True,
                    },
                    {
                        "id": "backend_build",
                        "name": "后端构建检查",
                        "layer": "backend",
                        "passed": True,
                        "required": True,
                    },
                ]
            },
        ) as build:
            result = build_code_review_subgraph().invoke(
                {
                    "workspace": workspace,
                    "status": "requires_user_input",
                    "code_review_result": scan,
                    "code_review_repair_confirmation": {"action": "repair_all"},
                    "code_review_repair_status": "awaiting_user",
                    "code_review_repair_iteration": 0,
                    "code_review_max_repair_iterations": MAX_CODE_REVIEW_REPAIR_ITERATIONS,
                }
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["code_review_repair_status"], "completed")
        self.assertEqual(
            result["code_review_next_action"], "acceptance_phase_confirmation"
        )
        analyze.assert_not_called()
        build.assert_called_once()

    def test_out_of_scope_repair_diff_blocks_launch(self) -> None:
        """修复 Agent 产生配置文件 Diff 时必须失败并阻断项目启动。"""

        issue = _issue()
        captured = CapturedWorkspaceChanges(
            value={
                "status": "completed",
                "summary": "尝试修复。",
                "attempted_issue_ids": [issue["id"]],
                "changed_files": ["package.json"],
                "failure_reason": None,
            },
            code_change_set={
                "id": "code-change-set:unsafe",
                "status": "applied",
                "files": [{"path": "package.json", "changeType": "modified"}],
            },
        )
        state = {
            "workspace": tempfile.mkdtemp(),
            "status": "requires_user_input",
            "code_review_result": _scan_result(issues=[issue]),
            "code_review_repair_iteration": 0,
            "code_review_max_repair_iterations": 3,
        }
        with patch(
            "app.graph.subgraphs.code_review.capture_agent_file_changes",
            return_value=captured,
        ):
            result = code_review_repair(state)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["code_review_next_action"], "handle_failure")
        self.assertIn("越界", result["error"])

    def test_frontend_dependency_repair_requires_successful_pnpm_evidence(self) -> None:
        """依赖问题缺少专用 pnpm 成功证据时必须阻断修复。"""

        issue = _frontend_dependency_issue()
        captured = CapturedWorkspaceChanges(
            value={
                "agent_output": {
                    "status": "completed",
                    "summary": "已修改 package.json。",
                    "attempted_issue_ids": [issue["id"]],
                    "changed_files": ["frontend/package.json"],
                    "failure_reason": None,
                },
                "pnpm_install_call_count": 0,
                "pnpm_install_called": False,
                "pnpm_install_completed": False,
                "pnpm_install_failed": False,
                "pnpm_install": None,
            },
            code_change_set={
                "id": "code-change-set:missing-install",
                "status": "applied",
                "files": [
                    {"path": "frontend/package.json", "changeType": "modified"}
                ],
            },
        )
        state = {
            "workspace": tempfile.mkdtemp(),
            "status": "requires_user_input",
            "code_review_result": _scan_result(issues=[issue]),
            "code_review_repair_iteration": 0,
            "code_review_max_repair_iterations": 3,
        }
        with patch(
            "app.graph.subgraphs.code_review.capture_agent_file_changes",
            return_value=captured,
        ):
            result = code_review_repair(state)

        self.assertEqual(result["status"], "failed")
        self.assertIn("pnpm install", result["error"])

    def test_frontend_dependency_repair_rejects_failed_pnpm_call(self) -> None:
        """专用 pnpm 工具被调用但执行失败时不能接受 Agent 的 completed 声明。"""

        issue = _frontend_dependency_issue()
        captured = CapturedWorkspaceChanges(
            value={
                "agent_output": {
                    "status": "completed",
                    "summary": "安装失败但仍声明完成。",
                    "attempted_issue_ids": [issue["id"]],
                    "changed_files": ["frontend/package.json"],
                    "failure_reason": None,
                },
                "pnpm_install_call_count": 1,
                "pnpm_install_called": True,
                "pnpm_install_completed": False,
                "pnpm_install_failed": True,
                "pnpm_install": {
                    "execution_id": "install-failed",
                    "status": "failed",
                    "exit_code": 1,
                    "timed_out": False,
                    "command": ["pnpm", "install"],
                    "cwd": "frontend",
                },
            },
            code_change_set={
                "id": "code-change-set:failed-install",
                "status": "applied",
                "files": [
                    {"path": "frontend/package.json", "changeType": "modified"}
                ],
            },
        )
        state = {
            "workspace": tempfile.mkdtemp(),
            "status": "requires_user_input",
            "code_review_result": _scan_result(issues=[issue]),
            "code_review_repair_iteration": 0,
            "code_review_max_repair_iterations": 3,
        }
        with patch(
            "app.graph.subgraphs.code_review.capture_agent_file_changes",
            return_value=captured,
        ):
            result = code_review_repair(state)

        self.assertEqual(result["status"], "failed")
        self.assertIn("成功执行证据", result["error"])

    def test_frontend_dependency_repair_accepts_generated_lockfile(self) -> None:
        """专用 pnpm 工具成功后应接受 package 与 lockfile 的真实 Diff。"""

        issue = _frontend_dependency_issue()
        evidence = {
            "execution_id": "install-1",
            "status": "passed",
            "exit_code": 0,
            "timed_out": False,
            "command": ["pnpm", "install"],
            "cwd": "frontend",
            "stdout_log": ".xcodeagent/runtime/code-review/pnpm-install/install-1/stdout.log",
            "stderr_log": ".xcodeagent/runtime/code-review/pnpm-install/install-1/stderr.log",
            "stdout_tail": "done",
            "stderr_tail": "",
        }
        captured = CapturedWorkspaceChanges(
            value={
                "agent_output": {
                    "status": "completed",
                    "summary": "已按 Skill 更新依赖。",
                    "attempted_issue_ids": [issue["id"]],
                    "changed_files": [
                        "frontend/package.json",
                        "frontend/pnpm-lock.yaml",
                    ],
                    "failure_reason": None,
                },
                "pnpm_install_call_count": 1,
                "pnpm_install_called": True,
                "pnpm_install_completed": True,
                "pnpm_install_failed": False,
                "pnpm_install": evidence,
            },
            code_change_set={
                "id": "code-change-set:dependency",
                "status": "applied",
                "files": [
                    {"path": "frontend/package.json", "changeType": "modified"},
                    {"path": "frontend/pnpm-lock.yaml", "changeType": "modified"},
                ],
            },
        )
        with tempfile.TemporaryDirectory() as workspace:
            frontend = Path(workspace) / "frontend"
            frontend.mkdir()
            (frontend / "pnpm-lock.yaml").write_text("lockfileVersion: '6.0'", encoding="utf-8")
            state = {
                "workspace": workspace,
                "status": "requires_user_input",
                "code_review_result": _scan_result(issues=[issue]),
                "code_review_repair_iteration": 0,
                "code_review_max_repair_iterations": 3,
            }
            with patch(
                "app.graph.subgraphs.code_review.capture_agent_file_changes",
                return_value=captured,
            ):
                result = code_review_repair(state)

        self.assertEqual(result["status"], "in_progress")
        self.assertEqual(
            result["code_review_repair_result"]["package_install"]["status"],
            "passed",
        )
        self.assertIn(
            "frontend/pnpm-lock.yaml",
            result["code_review_repair_result"]["changed_files"],
        )

    def test_frontend_dependency_repair_rejects_multiple_pnpm_calls(self) -> None:
        """依赖规则即使最终成功，调用专用 pnpm 工具超过一次也必须失败。"""

        issue = _frontend_dependency_issue()
        captured = CapturedWorkspaceChanges(
            value={
                "agent_output": {
                    "status": "completed",
                    "summary": "重复执行了安装。",
                    "attempted_issue_ids": [issue["id"]],
                    "changed_files": [
                        "frontend/package.json",
                        "frontend/pnpm-lock.yaml",
                    ],
                    "failure_reason": None,
                },
                "pnpm_install_call_count": 2,
                "pnpm_install_called": True,
                "pnpm_install_completed": True,
                "pnpm_install_failed": False,
                "pnpm_install": {
                    "execution_id": "install-2",
                    "status": "passed",
                    "exit_code": 0,
                    "timed_out": False,
                    "command": ["pnpm", "install"],
                    "cwd": "frontend",
                },
            },
            code_change_set={
                "id": "code-change-set:repeated-install",
                "status": "applied",
                "files": [
                    {"path": "frontend/package.json", "changeType": "modified"},
                    {"path": "frontend/pnpm-lock.yaml", "changeType": "modified"},
                ],
            },
        )
        with tempfile.TemporaryDirectory() as workspace:
            frontend = Path(workspace) / "frontend"
            frontend.mkdir()
            (frontend / "pnpm-lock.yaml").write_text("lockfileVersion: '6.0'", encoding="utf-8")
            state = {
                "workspace": workspace,
                "status": "requires_user_input",
                "code_review_result": _scan_result(issues=[issue]),
                "code_review_repair_iteration": 0,
                "code_review_max_repair_iterations": 3,
            }
            with patch(
                "app.graph.subgraphs.code_review.capture_agent_file_changes",
                return_value=captured,
            ):
                result = code_review_repair(state)

        self.assertEqual(result["status"], "failed")
        self.assertIn("恰好执行一次", result["error"])

    def test_repair_node_reports_building_until_checks_finish(self) -> None:
        """修复产生真实 Diff 后必须进入 building，不能在构建前提前标记完成。"""

        issue = _issue()
        captured = CapturedWorkspaceChanges(
            value={
                "status": "completed",
                "summary": "已修复连接超时配置。",
                "attempted_issue_ids": [issue["id"]],
                "changed_files": [issue["file"]],
                "failure_reason": None,
            },
            code_change_set={
                "id": "code-change-set:building",
                "status": "applied",
                "files": [{"path": issue["file"], "changeType": "modified"}],
            },
        )
        state = {
            "workspace": tempfile.mkdtemp(),
            "status": "requires_user_input",
            "code_review_result": _scan_result(issues=[issue]),
            "code_review_repair_iteration": 0,
            "code_review_max_repair_iterations": MAX_CODE_REVIEW_REPAIR_ITERATIONS,
        }

        with patch(
            "app.graph.subgraphs.code_review.capture_agent_file_changes",
            return_value=captured,
        ):
            result = code_review_repair(state)

        self.assertEqual(result["code_review_repair_status"], "building")
        self.assertEqual(result["code_review_repair_result"]["status"], "building")

    @patch("app.graph.subgraphs.code_review.capture_agent_file_changes")
    def test_repair_budget_never_allows_fourth_agent_call(self, capture_mock) -> None:
        """恢复快照即使扩大预算，也不能触发第四轮修复 Agent。"""

        result = code_review_repair(
            {
                "workspace": tempfile.mkdtemp(),
                "status": "requires_user_input",
                "code_review_result": _scan_result(issues=[_issue()]),
                "code_review_repair_iteration": 3,
                "code_review_max_repair_iterations": 99,
            }
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["code_review_next_action"], "handle_failure")
        self.assertIn("最大 3 轮", result["error"])
        capture_mock.assert_not_called()

    def test_start_route_requires_issue_snapshot_for_repair(self) -> None:
        """缺少有效问题快照时 repair_all 只能回到首次扫描入口。"""

        self.assertEqual(
            _route_review_start(
                {
                    "code_review_repair_confirmation": {"action": "repair_all"},
                    "code_review_result": {"issues": []},
                }
            ),
            "code_scan",
        )

    def test_public_build_checks_keep_three_review_steps(self) -> None:
        """审查构建投影始终保留依赖安装、前端构建和后端构建三步。"""

        checks = _public_build_checks(
            [
                {
                    "id": "frontend_install",
                    "name": "前端依赖安装检查",
                    "layer": "frontend",
                    "passed": True,
                    "required": True,
                }
            ]
        )
        self.assertEqual(
            [check["id"] for check in checks],
            ["frontend_install", "frontend_build", "backend_build"],
        )
        self.assertEqual(checks[0]["status"], "passed")
        self.assertEqual(checks[1]["status"], "skipped")
        self.assertEqual(checks[2]["status"], "skipped")

    def test_failed_review_build_returns_to_repair_before_launch(self) -> None:
        """任一必需审查构建失败时必须回到修复轮次，不能提前启动项目。"""

        with patch(
            "app.graph.subgraphs.code_review.run_integration_checks",
            return_value={
                "test_results": [
                    {
                        "id": "frontend_install",
                        "name": "前端依赖安装检查",
                        "passed": True,
                        "required": True,
                    },
                    {
                        "id": "frontend_build",
                        "name": "前端构建检查",
                        "passed": False,
                        "required": True,
                        "evidence": "构建失败",
                    },
                    {
                        "id": "backend_build",
                        "name": "后端构建检查",
                        "passed": True,
                        "required": True,
                    },
                ]
            },
        ):
            result = review_build_checks(
                {
                    "workspace": tempfile.mkdtemp(),
                    "code_review_result": _scan_result(issues=[_issue()]),
                    "code_review_repair_iteration": 1,
                    "code_review_max_repair_iterations": 3,
                    "code_review_repair_result": {"status": "building"},
                }
            )

        self.assertEqual(result["code_review_repair_status"], "repairing")
        self.assertEqual(result["code_review_next_action"], "code_review_repair")
        self.assertNotEqual(result["code_review_next_action"], "launch_project")

    def test_review_build_reuses_agent_pnpm_install_evidence(self) -> None:
        """构建检查应复用修复 Agent 的成功安装证据，避免重复安装。"""

        evidence = {
            "status": "passed",
            "exit_code": 0,
            "command": ["pnpm", "install"],
            "cwd": "frontend",
            "stdout_log": ".xcodeagent/runtime/code-review/pnpm-install/run/stdout.log",
            "stderr_log": ".xcodeagent/runtime/code-review/pnpm-install/run/stderr.log",
            "stdout_tail": "done",
            "stderr_tail": "",
        }
        with patch(
            "app.graph.subgraphs.code_review.run_integration_checks",
            return_value={
                "test_results": [
                    {
                        "id": "frontend_install",
                        "name": "前端依赖安装检查",
                        "passed": True,
                        "required": True,
                    },
                    {
                        "id": "frontend_build",
                        "name": "前端构建检查",
                        "passed": True,
                        "required": True,
                    },
                    {
                        "id": "backend_build",
                        "name": "后端构建检查",
                        "passed": True,
                        "required": True,
                    },
                ]
            },
        ) as checks:
            result = review_build_checks(
                {
                    "workspace": tempfile.mkdtemp(),
                    "code_review_result": _scan_result(issues=[_frontend_dependency_issue()]),
                    "code_review_repair_iteration": 1,
                    "code_review_max_repair_iterations": 3,
                    "code_review_repair_result": {
                        "status": "building",
                        "package_install": evidence,
                    },
                }
            )

        self.assertEqual(result["status"], "completed")
        supplied = checks.call_args.kwargs["frontend_install_result"]
        self.assertEqual(supplied["id"], "frontend_install")
        self.assertEqual(supplied["execution"]["tool"], "pnpm_install_frontend")


if __name__ == "__main__":
    unittest.main()
