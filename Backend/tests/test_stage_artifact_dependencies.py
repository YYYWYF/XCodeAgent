"""按阶段读取磁盘输入，无关下游损坏不能阻断上游重新生成。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.stage_artifacts import _ARTIFACTS, _STAGES, load_stage_artifacts


class StageArtifactDependencyTests(unittest.TestCase):
    """验证规划依赖边界、源码扫描隔离及必要输入损坏的显式失败。"""

    def write(self, root, relative, content):
        """把指定文件写到隔离工作区的当前正式产物位置。"""
        path = Path(root, ".devagentstudio", relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_upstream_generation_ignores_corrupt_downstream(self):
        """需求、产品和 UI 只读取自身及前置输入，未读投影不能残留旧 State。"""
        cases = {
            "requirements": {"requirement_spec"},
            "product_planning": {"requirement_spec", "product_plan"},
            "ui_confirmation": {"requirement_spec", "product_plan", "ui_designs"},
        }
        for node, required in cases.items():
            with self.subTest(node=node), tempfile.TemporaryDirectory() as root:
                for key, relative in _ARTIFACTS.items():
                    self.write(root, relative, json.dumps({"loaded": key}) if key in required else "{")
                fields = load_stage_artifacts({"workspace": root, "technical_plan": {"stale": True}}, node)
                for key in _ARTIFACTS:
                    self.assertEqual(fields[key], {"loaded": key} if key in required else {})
                    if key not in required:
                        self.assertEqual(fields[f"{key}_path"], "")
                        self.assertEqual(fields[f"{key}_json_path"], "")
                self.assertEqual(fields["project_plan"], {})
                self.assertEqual(fields["project_plan_path"], "")
                self.assertEqual(fields["project_plan_json_path"], "")

    def test_requirements_does_not_even_read_product_draft(self):
        """产品草稿也是需求节点的下游，损坏时仍可重新分析需求。"""
        with tempfile.TemporaryDirectory() as root:
            self.write(root, "drafts/specs/requirement-spec.json", '{"draft":true}')
            self.write(root, "drafts/plans/product-plan.json", "{")
            self.write(root, "plans/technical-plan.json", "{")
            fields = load_stage_artifacts({"workspace": root}, "requirements")
            self.assertEqual(fields["requirement_spec"], {"draft": True})
            self.assertEqual(fields["product_plan"], {})

    def test_required_upstream_corruption_is_not_silently_discarded(self):
        """真正消费的前置文档损坏仍报错，不以空输入伪造成功或绕过确认。"""
        with tempfile.TemporaryDirectory() as root:
            self.write(root, "plans/product-plan.json", "{")
            with self.assertRaises(ValueError):
                load_stage_artifacts({"workspace": root}, "ui_confirmation")

    def test_all_registered_stages_have_expected_disk_read_boundaries(self):
        """逐一验证实际读取集合，扫描不读正式产物，API 检查不提前读任务计划。"""
        planning = {
            "requirements": {"requirement_spec"},
            "product_planning": {"requirement_spec", "product_plan"},
            "ui_confirmation": {"requirement_spec", "product_plan", "ui_designs"},
        }
        source_only = {"inspect_workspace", "scan_workspace_code", "scan_change_impact_code", "classify_intent",
            "execute_frontend", "execute_backend", "validate_direct_fix",
            "direct_modification_repair", "finalize_direct_modification"}
        without_tasks = source_only | set(planning) | {"technical_planning_begin", "technical_planning_generate",
            "technical_planning_commit", "technical_planning_confirm", "template_reconcile", "project_planning",
            "api_design_readiness_gate", "authorization_bootstrap"}
        with tempfile.TemporaryDirectory() as root:
            for node in _STAGES:
                with self.subTest(node=node), patch.object(Path, "is_file", return_value=True), \
                        patch("app.services.stage_prerequisites.require_stage_inputs"), \
                        patch("app.services.stage_artifacts.prepare_generation_baseline", return_value=True), \
                        patch("app.services.stage_artifacts.load_project_plan_json", return_value={}) as read, \
                        patch("app.workspace.task_documents.load_confirmed_build_task_plan", return_value={}) as tasks, \
                        patch("app.services.development_review_files.development_review_files", return_value=[]), \
                        patch("app.services.stage_execution_artifacts.load_stage_execution_artifacts", return_value={}):
                    load_stage_artifacts({"workspace": root}, node)
                    expected = set() if node in source_only else planning.get(node, set(_ARTIFACTS))
                    self.assertEqual({call.args[0].name for call in read.call_args_list},
                                     {Path(_ARTIFACTS[key]).name for key in expected})
                    self.assertEqual(tasks.call_count, 0 if node in without_tasks else 1)
