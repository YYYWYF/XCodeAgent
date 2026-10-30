"""业务验收按当前正式接口双文件定位来源，不依赖 TechnicalPlan 内联设计。"""

from datetime import UTC, datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from app.domain.api_design import EndpointFieldMappingDesign
from app.services.business_acceptance import _endpoint_detail_sources
from app.services.business_acceptance_verifier import _source_hash_errors
from app.services.build_business_retry import restored_source_task_ids
from app.services.build_scheduler import reset_failed_tasks_for_retry, select_ready_build_batch
from app.workspace.endpoint_design_documents import (
    endpoint_design_paths, read_endpoint_design, technical_plan_sha256, write_endpoint_design,
)


class BusinessSourceDiskResolutionTests(TestCase):
    """真实读写正式产物验证缺失、过期与复合身份，不调用模型或数据库。"""

    def setUp(self):
        """构造仅含接口契约的技术规划，设计独立保存。"""
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.endpoint = {"id": "leave_balance_api.query", "kind": "business", "method": "GET", "path": "/balance"}
        self.plan = {"api_contracts": [{"id": "leave_balance_api", "endpoints": [self.endpoint]}],
                     "confirmation_status": "confirmed"}
        self.path = self.root / ".devagentstudio/plans/technical-plan.json"
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps(self.plan), encoding="utf-8")
        self._write_design("初次设计", "a" * 32)
        design = read_endpoint_design(self.root, "leave_balance_api", self.endpoint["id"])
        self.check = {"sources": _endpoint_detail_sources({"endpoint_designs": [design]})}

    def _write_design(self, description, revision):
        """写入当前版正式双文件，摘要与平台正常保存路径一致。"""
        write_endpoint_design(self.root, EndpointFieldMappingDesign.model_validate({
            "apiContractId": "leave_balance_api", "endpointId": self.endpoint["id"], "endpointContract": self.endpoint,
            "artifactRevision": revision, "implementationDescription": description, "fieldMappings": [],
            "basedOn": [{"artifactKey": "technical-plan", "sha256": technical_plan_sha256(self.root)}],
            "confirmedAt": datetime.now(UTC)}))

    def test_resolves_from_disk_without_inline_design(self):
        """复现执行阶段只有 TechnicalPlan 时仍能找到独立正式设计。"""
        self.assertNotIn("endpoint_designs", self.plan)
        self.assertEqual(_source_hash_errors(self.check, self.plan, workspace_root=self.root), [])

    def test_changed_design_blocks_instead_of_trusting_old_inline_snapshot(self):
        """磁盘设计已改变时不能拿任务内旧设计自证摘要正确。"""
        old = read_endpoint_design(self.root, "leave_balance_api", self.endpoint["id"])
        self._write_design("修改业务规则", "b" * 32)
        errors = _source_hash_errors(self.check, {**self.plan, "endpoint_designs": [old]}, workspace_root=self.root)
        self.assertTrue(any("哈希已变化" in error for error in errors))

    def test_missing_markdown_blocks_even_if_inline_design_exists(self):
        """双文件不完整时保持阻断，不能从内存缓存补证。"""
        old = read_endpoint_design(self.root, "leave_balance_api", self.endpoint["id"])
        _, markdown = endpoint_design_paths(self.root, "leave_balance_api", self.endpoint["id"])
        markdown.write_text("人工修改", encoding="utf-8")
        errors = _source_hash_errors(self.check, {**self.plan, "endpoint_designs": [old]}, workspace_root=self.root)
        self.assertTrue(any("无法定位正式来源" in error for error in errors))

    def test_composite_source_does_not_resolve_another_contract_with_same_endpoint_id(self):
        """相同短 ID 的契约并存时，来源显式契约身份保持唯一定位。"""
        plan = {**self.plan, "api_contracts": [*self.plan["api_contracts"],
                {"id": "another_api", "endpoints": [self.endpoint]}]}
        self.assertEqual(_source_hash_errors(self.check, plan, workspace_root=self.root), [])

    def _blocked_task(self, reason="formal_source_unavailable"):
        """构造与现场相同的来源阻断任务，保留平台验收证据。"""
        return {"id": "backend:bootstrap::bootstrap", "status": "failed", "dependencies": [],
                "failure_category": "business_acceptance_blocked",
                "business_acceptance_checks": [{**self.check, "id": "source-check"}],
                "business_acceptance_evidence": [{"check_id": "source-check", "status": "blocked",
                                                  "facts": {"reason_code": reason}}]}

    def test_explicit_retry_releases_restored_source_to_dispatch(self):
        """来源恢复后旧终止失败应进入待派发，不能继续显示 no_candidates。"""
        task = self._blocked_task()
        ids = restored_source_task_ids([task], formal_artifacts=self.plan, workspace_root=self.root)
        reset = reset_failed_tasks_for_retry([task], ids)
        self.assertEqual(select_ready_build_batch(reset)["ready_task_ids"], [task["id"]])
        self.assertEqual(reset[0]["retry_count"], 1)
        self.assertEqual(task["status"], "failed")

    def test_retry_does_not_release_changed_formal_source(self):
        """来源可读取但内容变化时仍须重新规划，不能重试旧合同。"""
        self._write_design("调整正式规则", "c" * 32)
        self.assertEqual(restored_source_task_ids([self._blocked_task()], formal_artifacts=self.plan,
                                                workspace_root=self.root), set())

    def test_retry_does_not_release_missing_or_non_source_block(self):
        """缺失文件、其他业务阻断和已完成任务均不能被来源恢复逻辑放行。"""
        task = self._blocked_task("unsupported_capability")
        self.assertEqual(restored_source_task_ids([task], formal_artifacts=self.plan,
                                                workspace_root=self.root), set())
        completed = {**self._blocked_task(), "status": "completed"}
        self.assertEqual(restored_source_task_ids([completed], formal_artifacts=self.plan,
                                                workspace_root=self.root), set())
        _, markdown = endpoint_design_paths(self.root, "leave_balance_api", self.endpoint["id"])
        markdown.write_text("人工修改", encoding="utf-8")
        self.assertEqual(restored_source_task_ids([self._blocked_task()], formal_artifacts=self.plan,
                                                workspace_root=self.root), set())
