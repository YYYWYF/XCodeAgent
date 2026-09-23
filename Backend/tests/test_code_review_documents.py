from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.workspace.code_review_documents import (
    CODE_REVIEW_REPORT_RELATIVE_PATH,
    render_code_review_markdown,
    write_code_review_markdown,
)


class CodeReviewDocumentsTests(unittest.TestCase):
    """验证代码审查报告只公开扫描汇总和安全的问题详情。"""

    def test_report_summarizes_counts_without_scan_file_names_or_internal_evidence(self) -> None:
        """报告必须汇总前后端数量，但不能包含扫描文件清单和内部修复动作。"""

        with tempfile.TemporaryDirectory() as workspace:
            result = {
                "status": "completed",
                "issue_count": 1,
                "targets": [
                    {
                        "side": "frontend",
                        "root": "frontend",
                        "status": "completed",
                        "scanned_file_count": 12,
                        "warning": (
                            f"{workspace}/frontend 中有一项提示，"
                            "日志位于 /Users/reviewer/private.log"
                        ),
                        "scanned_files": ["frontend/src/SecretInventory.tsx"],
                    },
                    {
                        "side": "backend",
                        "root": "backend/src/main/java",
                        "status": "completed",
                        "scanned_file_count": 8,
                    },
                ],
                "issues": [
                    {
                        "id": "FE001-1",
                        "side": "frontend",
                        "severity": "high",
                        "rule_id": "FE001",
                        "title": "依赖版本存在风险",
                        "summary": "请升级依赖。",
                        "file": "frontend/package.json",
                        "line": 10,
                        "repair_actions": ["pnpm_install"],
                        "execution_log": "private log",
                    }
                ],
            }

            content = render_code_review_markdown({"workspace": workspace}, result)
            report_path = write_code_review_markdown({"workspace": workspace}, result)

            self.assertEqual(
                Path(report_path).resolve().relative_to(Path(workspace).resolve()).as_posix(),
                CODE_REVIEW_REPORT_RELATIVE_PATH,
            )
            self.assertIn("| 前端 | `frontend` | 已完成 | 12 |", content)
            self.assertIn("| 后端 | `backend/src/main/java` | 已完成 | 8 |", content)
            self.assertIn("前后端扫描文件总数：20", content)
            self.assertIn("`frontend/package.json:10`", content)
            self.assertNotIn("SecretInventory.tsx", content)
            self.assertNotIn("pnpm_install", content)
            self.assertNotIn("private log", content)
            self.assertNotIn(workspace, content)
            self.assertNotIn("/Users/reviewer/private.log", content)

    def test_clean_report_has_explicit_pass_conclusion(self) -> None:
        """无问题报告必须给出明确通过结论。"""

        with tempfile.TemporaryDirectory() as workspace:
            content = render_code_review_markdown(
                {"workspace": workspace},
                {
                    "status": "completed",
                    "issue_count": 0,
                    "targets": [],
                    "issues": [],
                },
            )

        self.assertIn("审查通过，未发现需要处理的问题", content)
        self.assertIn("代码审查通过", content)

    def test_diff_report_lists_every_reviewed_file(self) -> None:
        """Diff 报告必须逐项展示服务端确认的全部实际审查文件。"""

        with tempfile.TemporaryDirectory() as workspace:
            content = render_code_review_markdown(
                {"workspace": workspace},
                {
                    "status": "completed",
                    "review_mode": "diff",
                    "review_file_count": 3,
                    "review_files": [
                        "frontend/src/apis/ageApi.ts",
                        "backend/src/main/java/example/AgeController.java",
                        "backend/src/main/resources/mapper/AgeRecordMapper.xml",
                    ],
                    "targets": [],
                    "issues": [],
                },
            )

        self.assertIn("## Diff 审查文件", content)
        self.assertIn("- `frontend/src/apis/ageApi.ts`", content)
        self.assertIn(
            "- `backend/src/main/java/example/AgeController.java`", content
        )
        self.assertIn(
            "- `backend/src/main/resources/mapper/AgeRecordMapper.xml`", content
        )

    def test_full_report_does_not_render_diff_file_list(self) -> None:
        """全量审查保持原报告结构，不展示 Diff 文件章节。"""

        with tempfile.TemporaryDirectory() as workspace:
            content = render_code_review_markdown(
                {"workspace": workspace},
                {
                    "status": "completed",
                    "review_mode": "full",
                    "review_files": ["frontend/src/App.tsx"],
                    "targets": [],
                    "issues": [],
                },
            )

        self.assertNotIn("## Diff 审查文件", content)
        self.assertNotIn("frontend/src/App.tsx", content)


if __name__ == "__main__":
    unittest.main()
