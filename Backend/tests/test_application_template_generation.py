from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.application_template_generation import (
    ApplicationTemplateGenerationError,
    load_template_generation_manifest,
    prepare_application_template_generation,
    validate_application_template_generation,
)


class ApplicationTemplateGenerationTests(unittest.TestCase):
    """验证页面/菜单增量初始化、manifest 和完成门禁。"""

    def _write_workspace(
        self,
        workspace: Path,
        *,
        pages: list[dict[str, str]],
        ui_status: str = "skipped",
    ) -> None:
        """写入模板测试所需的最小正式产物和前后端工程入口。"""

        plans = workspace / ".xcodeagent/plans"
        specs = workspace / ".xcodeagent/specs"
        plans.mkdir(parents=True)
        specs.mkdir(parents=True)
        (plans / "product-plan.json").write_text(
            json.dumps(
                {
                    "schema_version": "product-plan.v5",
                    "confirmation_status": "confirmed",
                    "pages": pages,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        ui_pages = [
            {
                "pageId": page["pageId"],
                "page_key": "".join(
                    segment[:1].upper() + segment[1:].lower()
                    for segment in page["pageId"].replace("-", "_").split("_")
                ),
            }
            for page in pages
        ]
        (specs / "ui-designs.json").write_text(
            json.dumps(
                {
                    "schema_version": "ui-manifest.v3",
                    "confirmation_status": ui_status,
                    "pages": ui_pages if ui_status == "confirmed" else [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (workspace / "frontend/src/constants").mkdir(parents=True)
        (workspace / "frontend/package.json").write_text("{}", encoding="utf-8")
        (workspace / "frontend/src/constants/menus.ts").write_text(
            "export const BIZ_MENUS = []\n", encoding="utf-8"
        )
        (workspace / "backend").mkdir()
        (workspace / "backend/pom.xml").write_text("<project />", encoding="utf-8")

    def _download_success(self) -> dict[str, object]:
        """返回两个模板目标都已可用的结构化下载结果。"""

        return {
            "ok": True,
            "status": "succeeded",
            "failedTargets": [],
            "targets": {
                "frontend": {"status": "succeeded", "attempt": 1},
                "backend": {"status": "succeeded", "attempt": 1},
            },
        }

    def test_incremental_rerun_preserves_page_and_does_not_duplicate_menu(self) -> None:
        """重复进入只补缺失项，不覆盖页面代码或重复菜单。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._write_workspace(
                workspace,
                pages=[{"pageId": "order_list", "name": "订单列表", "path": "/orders"}],
            )
            prepare_application_template_generation(workspace, self._download_success())
            page_path = workspace / "frontend/src/pages/OrderList/index.tsx"
            page_path.write_text("export default function Custom() { return null }\n", encoding="utf-8")

            manifest = prepare_application_template_generation(workspace, self._download_success())
            validated = validate_application_template_generation(workspace)
            menus = (workspace / "frontend/src/constants/menus.ts").read_text(encoding="utf-8")

            self.assertEqual(page_path.read_text(encoding="utf-8"), "export default function Custom() { return null }\n")
            self.assertEqual(menus.count('key: "OrderList"'), 1)
            self.assertIn('path: "/orders"', menus)
            self.assertEqual(manifest["steps"]["templateFiles"]["createdFiles"], [])
            self.assertEqual(validated["overall"]["status"], "succeeded")
            self.assertNotIn("apiSkeletons", validated["steps"])
            self.assertFalse((workspace / "frontend/src/services").exists())

    def test_latest_product_plan_is_reconciled_on_every_prepare(self) -> None:
        """ProductPlan 新增页面后，旧 manifest 不能放行，重新准备会增量补齐。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            pages = [{"pageId": "home", "name": "首页", "path": "/home"}]
            self._write_workspace(workspace, pages=pages)
            prepare_application_template_generation(workspace, self._download_success())
            pages.append({"pageId": "reports", "name": "报表", "path": "/reports"})
            (workspace / ".xcodeagent/plans/product-plan.json").write_text(
                json.dumps(
                    {
                        "schema_version": "product-plan.v5",
                        "confirmation_status": "confirmed",
                        "pages": pages,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ApplicationTemplateGenerationError, "页面占位缺失"):
                validate_application_template_generation(workspace)
            prepare_application_template_generation(workspace, self._download_success())
            validate_application_template_generation(workspace)

            self.assertTrue((workspace / "frontend/src/pages/Reports/index.tsx").is_file())

    def test_parallel_step_failure_keeps_the_other_step_result(self) -> None:
        """页面与菜单之一失败时仍应等待并记录另一项的完整结果。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._write_workspace(
                workspace,
                pages=[{"pageId": "orders", "name": "订单", "path": "/orders"}],
            )
            with patch(
                "app.services.application_template_generation.ensure_frontend_menu_entries",
                side_effect=OSError("menu locked"),
            ):
                with self.assertRaisesRegex(ApplicationTemplateGenerationError, "menu locked"):
                    prepare_application_template_generation(workspace, self._download_success())

            manifest = load_template_generation_manifest(workspace)
            self.assertEqual(manifest["steps"]["templateFiles"]["status"], "succeeded")
            self.assertEqual(manifest["steps"]["menus"]["status"], "failed")
            self.assertTrue((workspace / "frontend/src/pages/Orders/index.tsx").is_file())

    def test_confirmed_ui_page_key_must_match_shared_derivation(self) -> None:
        """UI 确认不能改变 ProductPlan pageId 对应的共享 PageKey。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._write_workspace(
                workspace,
                pages=[{"pageId": "order_list", "name": "订单", "path": "/orders"}],
                ui_status="confirmed",
            )
            ui_path = workspace / ".xcodeagent/specs/ui-designs.json"
            ui_manifest = json.loads(ui_path.read_text(encoding="utf-8"))
            ui_manifest["pages"][0]["page_key"] = "ChangedByUi"
            ui_path.write_text(json.dumps(ui_manifest), encoding="utf-8")

            with self.assertRaisesRegex(ApplicationTemplateGenerationError, "共享派生规则不一致"):
                prepare_application_template_generation(workspace, self._download_success())

    def test_download_failure_records_third_attempt_and_blocks_initialization(self) -> None:
        """第三次下载失败必须写入 manifest 并向上抛错。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            failed_download = {
                "ok": False,
                "status": "failed",
                "failedTargets": ["frontend"],
                "targets": {
                    "frontend": {
                        "status": "failed",
                        "attempt": 3,
                        "error": "network unavailable",
                    },
                    "backend": {"status": "pending", "attempt": 0},
                },
            }
            with self.assertRaisesRegex(ApplicationTemplateGenerationError, "模板下载未完成"):
                prepare_application_template_generation(workspace, failed_download)

            manifest = load_template_generation_manifest(workspace)
            self.assertEqual(manifest["steps"]["download"]["targets"]["frontend"]["attempt"], 3)
            self.assertEqual(manifest["steps"]["templateFiles"]["status"], "pending")
            self.assertEqual(manifest["steps"]["menus"]["status"], "pending")

    def test_download_manifest_records_template_source_without_authorization_artifact(self) -> None:
        """模板来源记录必须保留 URL、分支和提交，不另建权限专属产物。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._write_workspace(
                workspace,
                pages=[{"pageId": "orders", "name": "订单", "path": "/orders"}],
            )
            result = self._download_success()
            targets = result["targets"]
            assert isinstance(targets, dict)
            for name, url in {
                "frontend": "https://example.test/frontend.git",
                "backend": "https://example.test/backend.git",
            }.items():
                target = targets[name]
                assert isinstance(target, dict)
                target.update(
                    repositoryUrl=url,
                    branch="auth",
                    commitSha="a" * 40,
                )

            manifest = prepare_application_template_generation(workspace, result)

            for target_name in ("frontend", "backend"):
                source = manifest["steps"]["download"]["targets"][target_name]
                self.assertEqual(source["branch"], "auth")
                self.assertEqual(source["commitSha"], "a" * 40)
                self.assertTrue(source["repositoryUrl"].startswith("https://example.test/"))
            self.assertFalse((workspace / ".xcodeagent/authorization").exists())


if __name__ == "__main__":
    unittest.main()
