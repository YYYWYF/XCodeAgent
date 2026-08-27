from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from deepagents.backends.protocol import GlobResult, LsResult, ReadResult, WriteResult

from app.agents.code_analyze.analyzer import (
    REQUIRED_SKILL_PATHS,
    analyze_workspace_code,
    normalize_code_review_result,
)
from app.agents.code_analyze.scope import CodeAnalyzeScopedBackend, CodeReviewRepairScopedBackend


class CodeAnalyzeTests(unittest.TestCase):
    """验证代码审查 Agent 的目录边界和结果安全投影。"""

    def test_scope_reads_frontend_project_but_rejects_dependencies(self) -> None:
        """审查后端可读取前端项目文件，但拒绝依赖目录和所有写入。"""

        delegate = SimpleNamespace(
            ls=lambda *_args: LsResult(entries=[]),
            read=lambda *_args: ReadResult(error=None),
            write=lambda *_args: WriteResult(path="/frontend/src/App.tsx"),
        )
        scoped = CodeAnalyzeScopedBackend(delegate)

        self.assertIsNone(scoped.ls("frontend").error)
        self.assertIsNone(scoped.ls("frontend/src").error)
        self.assertIsNone(scoped.read("frontend/src/App.tsx").error)
        self.assertIsNone(scoped.read("frontend/package.json").error)
        self.assertEqual(
            scoped.read("frontend/node_modules/pkg/index.js").error,
            "code_analyze_path_denied: frontend/node_modules/pkg/index.js",
        )
        self.assertEqual(
            scoped.read("frontend/.env").error,
            "code_analyze_path_denied: frontend/.env",
        )
        self.assertEqual(scoped.write("frontend/src/App.tsx", "x").error, "code_analyze_write_denied")

    def test_scope_filters_recursive_frontend_list_results(self) -> None:
        """委托文件后端递归返回的依赖目录和敏感文件也不能暴露给扫描 Agent。"""

        delegate = SimpleNamespace(
            ls=lambda *_args: LsResult(
                entries=[
                    {"path": "/frontend/package.json", "is_dir": False},
                    {"path": "/frontend/.env", "is_dir": False},
                    {"path": "/frontend/node_modules/pkg", "is_dir": True},
                ]
            )
        )
        scoped = CodeAnalyzeScopedBackend(delegate)

        result = scoped.ls("frontend")

        self.assertEqual(
            [item["path"] for item in result.entries or []],
            ["/frontend/package.json"],
        )

    def test_scope_allows_rooted_code_glob_but_not_workspace_glob(self) -> None:
        """无 base path 的 glob 也必须显式锚定到两个源码根目录。"""

        delegate = SimpleNamespace(
            glob=lambda *_args: GlobResult(matches=[]),
        )
        scoped = CodeAnalyzeScopedBackend(delegate)

        self.assertIsNone(scoped.glob("/frontend/src/**/*.tsx").error)
        self.assertIn("code_analyze_path_denied", scoped.glob("**/*.tsx").error or "")

    def test_repair_scope_allows_frontend_project_but_protects_generated_files(self) -> None:
        """修复 Agent 可修改前端项目，但不能直接修改依赖目录或 lockfile。"""

        delegate = SimpleNamespace(
            write=lambda *_args: WriteResult(path="/frontend/src/App.tsx"),
            edit=lambda *_args: type("Edit", (), {"error": None})(),
        )
        scoped = CodeReviewRepairScopedBackend(delegate)

        self.assertIsNone(scoped.write("frontend/src/App.tsx", "x").error)
        self.assertIsNone(scoped.write("frontend/src/__tests__/App.test.tsx", "x").error)
        self.assertEqual(
            scoped.write("frontend/pnpm-lock.yaml", "x").error,
            "code_analyze_path_denied: frontend/pnpm-lock.yaml",
        )
        self.assertEqual(
            scoped.write("frontend/node_modules/pkg/index.js", "x").error,
            "code_analyze_path_denied: frontend/node_modules/pkg/index.js",
        )
        self.assertEqual(
            scoped.edit("backend/pom.xml", "old", "new").error,
            "code_analyze_path_denied: backend/pom.xml",
        )

    def test_normalizer_deduplicates_without_stale_frontend_warning(self) -> None:
        """后端问题去重且当前前端规则不再投影占位 warning。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            (root / "frontend/src").mkdir(parents=True)
            (root / "backend/src/main/java").mkdir(parents=True)
            result = normalize_code_review_result(
                {
                    "status": "completed",
                    "summary": "完成",
                    "loaded_skills": ["frontend-code-scan", "backend-code-scan"],
                    "targets": [
                        {
                            "side": "frontend",
                            "root": "frontend",
                            "status": "completed",
                            "scanned_file_count": 2,
                        },
                        {
                            "side": "backend",
                            "root": "backend/src/main/java",
                            "status": "completed",
                            "scanned_file_count": 2,
                        },
                    ],
                    "issues": [
                        {
                            "id": "one",
                            "side": "backend",
                            "rule_id": "CKR5000",
                            "severity": "HIGH",
                            "title": "重复问题",
                            "summary": "说明",
                            "file": "backend/src/main/java/App.java",
                            "line": 4,
                        },
                        {
                            "id": "two",
                            "side": "backend",
                            "rule_id": "CKR5000",
                            "severity": "high",
                            "title": "重复问题",
                            "summary": "另一段说明",
                            "file": "backend/src/main/java/App.java",
                            "line": 4,
                        },
                    ],
                    "truncated": False,
                },
                workspace=workspace,
            )

        self.assertEqual(result["issue_count"], 1)
        self.assertEqual(result["issues"][0]["severity"], "high")
        self.assertEqual(result["targets"][0]["status"], "completed")
        self.assertIsNone(result["targets"][0]["warning"])
        self.assertEqual(result["targets"][1]["status"], "completed")

    def test_normalizer_marks_missing_target_as_skipped(self) -> None:
        """源码目录不存在时应标记 skipped，且不影响另一端结果。"""

        with tempfile.TemporaryDirectory() as workspace:
            (Path(workspace) / "backend/src/main/java").mkdir(parents=True)
            result = normalize_code_review_result(
                {
                    "status": "completed",
                    "loaded_skills": ["frontend-code-scan", "backend-code-scan"],
                    "targets": [],
                    "issues": [],
                },
                workspace=workspace,
            )

        self.assertEqual(result["targets"][0]["status"], "skipped")
        self.assertEqual(result["targets"][0]["warning"], "扫描目录不存在，已跳过。")
        self.assertEqual(result["targets"][1]["status"], "completed")

    def test_normalizer_accepts_frontend_dependency_issue_and_repair_action(self) -> None:
        """前端依赖问题可指向 manifest，并保留有限 pnpm 修复动作。"""

        result = normalize_code_review_result(
            {
                "status": "completed",
                "summary": "发现前端依赖问题",
                "loaded_skills": ["frontend-code-scan", "backend-code-scan"],
                "targets": [{"side": "frontend", "root": "frontend"}],
                "issues": [
                    {
                        "side": "frontend",
                        "rule_id": "axios-version-risk",
                        "severity": "high",
                        "title": "axios 版本风险",
                        "summary": "需要按 Skill 修复。",
                        "file": "/frontend/package.json",
                        "repair_actions": ["pnpm_install"],
                    }
                ],
            }
        )

        self.assertEqual(result["issue_count"], 1)
        self.assertEqual(result["issues"][0]["file"], "frontend/package.json")
        self.assertEqual(result["issues"][0]["repair_actions"], ["pnpm_install"])

    def test_normalizer_accepts_scan_root_relative_frontend_manifest_paths(self) -> None:
        """前端依赖问题使用扫描根相对路径时应安全补全 frontend 根。"""

        result = normalize_code_review_result(
            {
                "status": "completed",
                "summary": "发现前端依赖问题",
                "loaded_skills": ["frontend-code-scan", "backend-code-scan"],
                "targets": [{"side": "frontend", "root": "frontend"}],
                "issues": [
                    {
                        "side": "frontend",
                        "rule_id": "axios-version-risk",
                        "severity": "high",
                        "title": "axios 版本风险",
                        "summary": "package.json 声明的版本存在风险。",
                        "file": "package.json",
                        "repair_actions": ["pnpm_install"],
                    },
                    {
                        "side": "frontend",
                        "rule_id": "form-data-version-risk",
                        "severity": "high",
                        "title": "form-data 版本风险",
                        "summary": "锁文件中的间接依赖版本存在风险。",
                        "file": "pnpm-lock.yaml",
                        "repair_actions": ["pnpm_install"],
                    },
                ],
            }
        )

        self.assertEqual(
            [issue["file"] for issue in result["issues"]],
            ["frontend/package.json", "frontend/pnpm-lock.yaml"],
        )

    def test_normalizer_keeps_side_relative_path_security_boundaries(self) -> None:
        """扫描根相对兼容不能放行依赖目录、跨端根或绝对越界路径。"""

        base = {
            "status": "completed",
            "loaded_skills": ["frontend-code-scan", "backend-code-scan"],
            "issues": [],
        }
        for file_path in (
            "node_modules/axios/index.js",
            "backend/src/main/java/App.java",
            "/etc/passwd",
            "../package.json",
        ):
            with self.subTest(file_path=file_path), self.assertRaisesRegex(
                ValueError,
                "越界源码路径|端类型与文件路径不匹配",
            ):
                normalize_code_review_result(
                    {
                        **base,
                        "issues": [
                            {
                                "side": "frontend",
                                "title": "越界问题",
                                "summary": "不应接受。",
                                "file": file_path,
                            }
                        ],
                    }
                )

    def test_normalizer_rejects_unregistered_repair_action(self) -> None:
        """Skill 输出不能借 repair_actions 扩展为任意命令权限。"""

        with self.assertRaisesRegex(ValueError, "未授权的修复动作"):
            normalize_code_review_result(
                {
                    "status": "completed",
                    "loaded_skills": ["frontend-code-scan", "backend-code-scan"],
                    "issues": [
                        {
                            "side": "frontend",
                            "title": "试图执行任意命令",
                            "summary": "不应接受",
                            "file": "frontend/package.json",
                            "repair_actions": ["execute_shell"],
                        }
                    ],
                }
            )

    def test_normalizer_rejects_frontend_node_modules_issue(self) -> None:
        """任何 node_modules 问题路径都不能进入公开扫描结果。"""

        with self.assertRaisesRegex(ValueError, "越界源码路径"):
            normalize_code_review_result(
                {
                    "status": "completed",
                    "loaded_skills": ["frontend-code-scan", "backend-code-scan"],
                    "issues": [
                        {
                            "side": "frontend",
                            "title": "越界依赖文件",
                            "summary": "不应展示",
                            "file": "frontend/node_modules/pkg/index.js",
                        }
                    ],
                }
            )

    def test_normalizer_accepts_logged_skill_and_target_shapes(self) -> None:
        """运行日志中的 Skill 文件对象和按端目标对象应归一为公开审查结构。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            (root / "frontend/src").mkdir(parents=True)
            (root / "backend/src/main/java").mkdir(parents=True)
            result = normalize_code_review_result(
                {
                    "status": "completed",
                    "loaded_skills": [
                        {
                            "name": "frontend-code-scan",
                            "path": "/.xcodeagent/builtin-skills/frontend-code-scan/SKILL.md",
                            "rules_loaded": 0,
                        },
                        {
                            "name": "backend-code-scan",
                            "path": "/.xcodeagent/builtin-skills/backend-code-scan/SKILL.md",
                            "rules_loaded": 8,
                            "references": "/.xcodeagent/builtin-skills/backend-code-scan/references/rules-reference.md",
                        },
                        {
                            "name": "backend-code-scan/rules-reference",
                            "path": "/.xcodeagent/builtin-skills/backend-code-scan/references/rules-reference.md",
                        },
                    ],
                    "targets": {
                        "frontend": {"root": "/frontend", "file_count": 30},
                        "backend": {
                            "root": "/backend/src/main/java",
                            "file_count": 17,
                        },
                    },
                    "issues": [],
                },
                workspace=workspace,
            )

        self.assertEqual(
            result["loaded_skills"],
            ["backend-code-scan", "frontend-code-scan"],
        )
        self.assertEqual(result["targets"][0]["scanned_file_count"], 30)
        self.assertEqual(result["targets"][1]["scanned_file_count"], 17)
        self.assertEqual(result["issues"], [])

    def test_normalizer_rejects_unapproved_nested_skill_reference(self) -> None:
        """Skill 对象中的嵌套规则引用仍须通过精确文件白名单。"""

        with self.assertRaisesRegex(ValueError, "未授权的扫描 Skill 规则引用"):
            normalize_code_review_result(
                {
                    "status": "completed",
                    "loaded_skills": [
                        "frontend-code-scan",
                        {
                            "name": "backend-code-scan",
                            "path": "backend-code-scan/SKILL.md",
                            "references": "backend-code-scan/references/other.md",
                        },
                    ],
                    "targets": [],
                    "issues": [],
                }
            )

    def test_normalizer_accepts_logged_skill_paths_and_flat_target_counts(self) -> None:
        """规则引用路径不是第三个 Skill，日志中的平铺扫描计数也应被保留。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            (root / "frontend/src").mkdir(parents=True)
            (root / "backend/src/main/java").mkdir(parents=True)
            result = normalize_code_review_result(
                {
                    "status": "completed",
                    "loaded_skills": [
                        "frontend-code-scan/SKILL.md",
                        "backend-code-scan/SKILL.md",
                        "backend-code-scan/references/rules-reference.md",
                    ],
                    "targets": {
                        "frontend_files_scanned": 22,
                        "backend_files_scanned": 17,
                    },
                    "issues": [],
                },
                workspace=workspace,
            )

        self.assertEqual(
            result["loaded_skills"],
            ["backend-code-scan", "frontend-code-scan"],
        )
        self.assertEqual(result["targets"][0]["scanned_file_count"], 22)
        self.assertEqual(result["targets"][1]["scanned_file_count"], 17)

    def test_normalizer_accepts_logged_scan_root_target_items(self) -> None:
        """最新日志中 targets 数组的 scan_root 字段应安全归一为固定 root。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            (root / "frontend/src").mkdir(parents=True)
            (root / "backend/src/main/java").mkdir(parents=True)
            result = normalize_code_review_result(
                {
                    "status": "completed",
                    "loaded_skills": [
                        "frontend-code-scan",
                        "backend-code-scan",
                    ],
                    "targets": [
                        {
                            "side": "frontend",
                            "scan_root": "/frontend/src",
                            "scanned_file_count": 0,
                            "warning": "前端 Skill 无规则。",
                        },
                        {
                            "side": "backend",
                            "scan_root": "/backend/src/main/java",
                            "scanned_file_count": 17,
                        },
                    ],
                    "issues": [],
                },
                workspace=workspace,
            )

        self.assertEqual(result["targets"][0]["root"], "frontend")
        self.assertEqual(result["targets"][0]["scanned_file_count"], 0)
        self.assertEqual(result["targets"][1]["root"], "backend/src/main/java")
        self.assertEqual(result["targets"][1]["scanned_file_count"], 17)

    def test_normalizer_rejects_out_of_scope_scan_root(self) -> None:
        """scan_root 别名不能绕过两个固定扫描目录的安全边界。"""

        with self.assertRaisesRegex(ValueError, "审查目标包含未授权目录"):
            normalize_code_review_result(
                {
                    "status": "completed",
                    "loaded_skills": [
                        "frontend-code-scan",
                        "backend-code-scan",
                    ],
                    "targets": [
                        {
                            "side": "backend",
                            "scan_root": "/backend/src/test/java",
                        }
                    ],
                    "issues": [],
                }
            )

    @patch("app.agents.create_agent_bundle")
    @patch("app.agents.code_analyze.analyzer.invoke_agent_with_tool_activity")
    def test_analyzer_does_not_rescan_after_result_validation_failure(
        self,
        invoke_mock,
        create_bundle_mock,
    ) -> None:
        """Agent 已完成扫描后，即使结果协议错误也不得再次执行整轮扫描。"""

        create_bundle_mock.return_value = SimpleNamespace(code_analyze=object())

        def invoke_once(*_args, on_tool_activity=None, **_kwargs):
            """模拟完整读取 Skill 后返回含未授权声明的结果。"""

            for path in REQUIRED_SKILL_PATHS:
                on_tool_activity(
                    {
                        "tool": "read_file",
                        "status": "completed",
                        "path": path,
                    }
                )
            return json.dumps(
                {
                    "status": "completed",
                    "loaded_skills": [
                        "frontend-code-scan",
                        "backend-code-scan",
                        "unapproved-skill",
                    ],
                    "targets": [],
                    "issues": [],
                }
            )

        invoke_mock.side_effect = invoke_once

        with self.assertRaisesRegex(ValueError, "未授权的扫描 Skill"):
            analyze_workspace_code({}, "/tmp/workspace")

        self.assertEqual(invoke_mock.call_count, 1)

    def test_normalizer_accepts_safe_workspace_path_variants(self) -> None:
        """虚拟根路径、点前缀和真实工作区内绝对路径应统一为安全相对路径。"""

        with tempfile.TemporaryDirectory() as workspace:
            source_root = Path(workspace) / "backend/src/main/java"
            source_root.mkdir(parents=True)
            absolute_file = source_root / "Example.java"
            result = normalize_code_review_result(
                {
                    "status": "completed",
                    "loaded_skills": ["frontend-code-scan", "backend-code-scan"],
                    "targets": [
                        {
                            "side": "backend",
                            "root": "/backend/src/main/java",
                            "status": "completed",
                        }
                    ],
                    "issues": [
                        {
                            "side": "backend",
                            "title": "问题一",
                            "summary": "说明",
                            "file": "/backend/src/main/java/Example.java",
                        },
                        {
                            "side": "backend",
                            "title": "问题二",
                            "summary": "说明",
                            "file": "./backend/src/main/java/Other.java",
                        },
                        {
                            "side": "backend",
                            "title": "问题三",
                            "summary": "说明",
                            "file": str(absolute_file),
                        },
                    ],
                },
                workspace=workspace,
            )

        self.assertEqual(
            [issue["file"] for issue in result["issues"]],
            [
                "backend/src/main/java/Example.java",
                "backend/src/main/java/Other.java",
                "backend/src/main/java/Example.java",
            ],
        )

    def test_normalizer_treats_findings_as_completed_review(self) -> None:
        """模型把发现问题标为 failed 时仍应投影成功审查和问题列表。"""

        with self.assertLogs("app.agents.code_analyze.analyzer", level="WARNING"):
            result = normalize_code_review_result(
                {
                    "status": "failed",
                    "loaded_skills": ["frontend-code-scan", "backend-code-scan"],
                    "targets": [
                        {
                            "side": "backend",
                            "root": "backend/src/main/java",
                            "status": "completed",
                            "scanned_file_count": 1,
                        }
                    ],
                    "issues": [
                        {
                            "side": "backend",
                            "rule_id": "CKR6002",
                            "severity": "high",
                            "title": "HttpURLConnection 未配置完整超时",
                            "summary": "连接未同时设置连接超时和读取超时。",
                            "file": "backend/src/main/java/PersonNameController.java",
                            "line": 69,
                        }
                    ],
                }
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["issue_count"], 1)
        self.assertEqual(result["issues"][0]["rule_id"], "CKR6002")

    def test_normalizer_rejects_failed_status_without_valid_findings(self) -> None:
        """没有有效问题支撑的 failed 状态仍表示扫描失败。"""

        base = {
            "loaded_skills": ["frontend-code-scan", "backend-code-scan"],
            "targets": [],
            "issues": [],
        }
        with self.assertRaises(ValueError):
            normalize_code_review_result({**base, "status": "failed"})

    def test_normalizer_rejects_absolute_out_of_scope_issue_path(self) -> None:
        """绝对越界问题路径不能借由状态归一进入公开结果。"""

        base = {
            "loaded_skills": ["frontend-code-scan", "backend-code-scan"],
            "targets": [],
            "issues": [],
        }
        with self.assertRaises(ValueError):
            normalize_code_review_result(
                {
                    **base,
                    "status": "completed",
                    "issues": [
                        {
                            "side": "backend",
                            "file": "/etc/passwd",
                            "title": "越界",
                            "summary": "越界",
                        }
                    ],
                }
            )

    def test_normalizer_redacts_absolute_paths_in_review_text(self) -> None:
        """摘要和问题说明中的宿主路径不能进入公开审查结果。"""

        with tempfile.TemporaryDirectory() as workspace:
            (Path(workspace) / "backend/src/main/java").mkdir(parents=True)
            result = normalize_code_review_result(
                {
                    "status": "completed",
                    "summary": f"扫描 {workspace}/frontend/src 完成",
                    "loaded_skills": ["frontend-code-scan", "backend-code-scan"],
                    "issues": [
                        {
                            "side": "backend",
                            "file": "backend/src/main/java/App.java",
                            "title": "绝对路径 {workspace}",
                            "summary": f"详情见 {workspace}/frontend/src/App.tsx",
                        }
                    ],
                },
                workspace=workspace,
            )

        self.assertNotIn(workspace, result["summary"])
        self.assertNotIn(workspace, result["issues"][0]["summary"])


if __name__ == "__main__":
    unittest.main()
