"""以真实双文件读写验证 Agent 接入确认、重复确认及能力变更边界。"""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from app.services.endpoint_design_detail import (
    EndpointDesignPrepareRequest, EndpointDesignSaveRequest, prepare_endpoint_design, save_endpoint_design,
)
from app.services.runtime_endpoint_contracts import materialize_runtime_endpoints
from app.workspace.endpoint_design_documents import endpoint_design_paths, technical_plan_sha256
from tests.test_runtime_endpoint_contracts import plan


class AgentEndpointConfirmationTests(TestCase):
    """不模拟保存成功，直接验证正式 JSON/Markdown 的版本与字节。"""

    def setUp(self):
        """创建当前正式 Runtime 技术规划，所有写入限制在临时工作区。"""
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.raw = plan()
        self.contract_id, self.endpoint_id = "agent.order_assistant", "agent.order_assistant.run"
        self._write_plan(self.raw)

    def _write_plan(self, raw):
        """物化当前运行接口并保存为已确认 TechnicalPlan。"""
        current = materialize_runtime_endpoints(raw)
        current["confirmation_status"] = "confirmed"
        target = self.root / ".devagentstudio/plans/technical-plan.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")

    def _prepare(self):
        """通过实际准备服务读取同一候选、版本与计划摘要。"""
        return prepare_endpoint_design(EndpointDesignPrepareRequest(workspaceRoot=str(self.root),
            apiContractId=self.contract_id, endpointId=self.endpoint_id))

    def _save(self, preparation):
        """模拟官方客户端提交当前审阅版本，不绕过保存服务。"""
        return save_endpoint_design(EndpointDesignSaveRequest(workspaceRoot=str(self.root),
            apiContractId=self.contract_id, endpointId=self.endpoint_id,
            draft=preparation["payload"]["draft"], baseRevision=preparation["artifactRevision"],
            technicalPlanHash=preparation["technicalPlanHash"]))

    def _bytes(self):
        """读取双文件原字节，检验重复确认没有另写一个版本。"""
        return tuple(path.read_bytes() for path in endpoint_design_paths(self.root, self.contract_id, self.endpoint_id))

    def test_first_confirmation_and_old_empty_revision_repeat_keep_same_artifact(self):
        """首次确认成功后旧窗口重复点击应返回相同版本，不误报他人修改。"""
        initial = self._prepare()
        self.assertIsNone(initial["artifactRevision"])
        first = self._save(initial)
        before = self._bytes()
        repeated = self._save(initial)
        self.assertEqual(repeated["artifactRevision"], first["artifactRevision"])
        self.assertEqual(repeated["detail"]["status"], "confirmed")
        self.assertEqual(before, self._bytes())
        fresh = self._prepare()
        self.assertEqual(fresh["artifactRevision"], first["artifactRevision"])
        self.assertEqual(self._save(fresh)["artifactRevision"], first["artifactRevision"])
        self.assertEqual(before, self._bytes())

    def test_changed_capability_is_not_acknowledged_as_the_old_confirmation(self):
        """能力变更仍须重新审阅，不能借幂等确认沿用旧候选。"""
        initial = self._prepare()
        first = self._save(initial)
        before = self._bytes()
        changed = deepcopy(self.raw)
        changed["agent_contracts"][0]["interactionContract"]["interactionKinds"] = ["clarification"]
        self._write_plan(changed)
        stale = {**initial, "technicalPlanHash": technical_plan_sha256(self.root)}
        with self.assertRaisesRegex(ValueError, "能力快照"):
            self._save(stale)
        self.assertEqual(before, self._bytes())
        renewed = self._save(self._prepare())
        self.assertNotEqual(renewed["artifactRevision"], first["artifactRevision"])
        self.assertEqual(renewed["detail"]["status"], "confirmed")

    def test_edited_markdown_is_not_overwritten_by_repeated_confirmation(self):
        """用户修改过的 Markdown 保持原样，重复确认不能恢复旧内容。"""
        initial = self._prepare()
        self._save(initial)
        _, markdown = endpoint_design_paths(self.root, self.contract_id, self.endpoint_id)
        markdown.write_text(markdown.read_text(encoding="utf-8") + "\n人工修改\n", encoding="utf-8")
        before = self._bytes()
        with self.assertRaisesRegex(ValueError, "双文件不一致|Markdown 已被编辑"):
            self._save(initial)
        self.assertEqual(before, self._bytes())
