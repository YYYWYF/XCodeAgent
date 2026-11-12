"""单任务计划修复的权限、业务不变量、过期确认和 AG-UI 失败生命周期回归。"""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from pydantic import ValidationError

from app.domain.build_task_repair import BuildTaskRepairInput, TaskRepairProposal
from app.protocols.build_task_repair import build_task_repair_stream
from app.services.build_task_repair import load_draft, save_draft, validate_draft
from app.services.build_task_repair_contract import compile_repaired_plan, repair_file, source_files
from app.services.build_task_repair_execution import rollback_repair_plan
from app.services.business_acceptance import compile_business_acceptance
from app.services.engineering_acceptance import compile_engineering_acceptance
from app.workspace.task_documents import build_task_plan_json_path, build_task_plan_sha256, write_json_atomic, load_build_task_plan_json
from tests.test_business_acceptance import _formal_context


class TaskRepairContractTests(unittest.TestCase):
    """使用真实编译器检查 business 补充 PO 权限时仍保留原业务职责。"""

    def setUp(self):
        """准备一个已完成 PO 任务和一个失败的接口业务任务。"""
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.workspace = self.directory.name
        self.context = _formal_context()
        self.po = "backend/src/main/java/com/example/orders/infrastructure/po/OrderPO.java"
        self.service = "backend/src/main/java/com/example/orders/application/OrderService.java"
        for name in (self.po, self.service):
            path = Path(self.workspace) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("class Existing {}", encoding="utf-8")
        task = {"id": "business", "unit_id": "backend:module:orders-api", "owner": "backend",
            "task_type": "backend.code", "title": "订单业务", "description": "实现订单字段映射",
            "dependencies": ["foundation"], "target_files": [self.service], "allowed_paths": [self.service],
            "change_scope": [{"path": self.service, "operation": "modify", "description": "实现业务"}],
            "deliverables": [{"id": "business:objects", "kind": "backend.objects", "target_id": "orders.list",
                              "paths": [self.service], "provides": ["backend.objects:orders.list"]}],
            "source_refs": {"backend_stage": "business", "endpoint_ids": ["orders.list"], "entity_ids": ["Order"]},
            "status": "failed", "can_run_in_parallel": True, "parallel_reason": "按文件锁调度",
            "impact_scope": {"summary": "订单", "affected_modules": [], "public_contracts": [], "risks": []}}
        foundation = {**deepcopy(task), "id": "foundation", "title": "既有 PO", "dependencies": [],
            "target_files": [self.po], "allowed_paths": [self.po], "status": "completed",
            "change_scope": [{"path": self.po, "operation": "add", "description": "准备 PO"}],
            "deliverables": [{**task["deliverables"][0], "id": "foundation:objects", "paths": [self.po]}]}
        tasks = compile_business_acceptance(compile_engineering_acceptance([foundation, task]), self.context)
        self.plan = {"schema_version": "build-dag.v4", "status": "ready", "confirmation_status": "confirmed",
                     "build_units": {}, "task_registry": {item["id"]: item for item in tasks}}
        self.state = {"workspace": self.workspace, "build_task_plan": self.plan}
        self.proposal = {"decision": "propose", "reason": "原业务任务遗漏 PO 文件权限",
            "repairInstructions": "按正式字段补齐 PO，并保留既有业务",
            "files": [{"path": self.po, "operation": "modify", "description": "补齐原契约中的字段",
                       "deliverableIds": ["business:objects"]}]}

    def test_business_can_modify_existing_po_without_rerunning_its_creator(self):
        """PO 可由当前业务任务修改，原创建任务保持完成且不获得新业务职责。"""
        baseline = deepcopy(self.plan)
        repaired = compile_repaired_plan(self.state, "business", TaskRepairProposal.model_validate(self.proposal), self.context)
        self.assertEqual(self.plan, baseline)
        task = repaired["task_registry"]["business"]
        self.assertIn(self.po, task["allowed_paths"])
        self.assertIn(self.po, task["lock_scope"])
        self.assertEqual(task["title"], baseline["task_registry"]["business"]["title"])
        self.assertTrue(task["description"].startswith(baseline["task_registry"]["business"]["description"]))
        self.assertEqual(task["change_scope"][0], baseline["task_registry"]["business"]["change_scope"][0])
        self.assertEqual(task["dependencies"], ["foundation"])
        self.assertEqual(task["source_refs"], baseline["task_registry"]["business"]["source_refs"])
        self.assertEqual(repaired["task_registry"]["foundation"]["status"], "completed")
        self.assertEqual(repaired["task_registry"]["foundation"]["deliverables"], baseline["task_registry"]["foundation"]["deliverables"])
        self.assertEqual((Path(self.workspace) / self.po).read_text(), "class Existing {}")

    def test_other_unit_file_cannot_be_claimed(self):
        """其他模块已认领的文件需要重规划，不能伪装成单任务补充。"""
        self.plan["task_registry"]["foundation"]["unit_id"] = "backend:module:another-api"
        with self.assertRaisesRegex(ValueError, "其他 Unit"):
            compile_repaired_plan(self.state, "business", TaskRepairProposal.model_validate(self.proposal), self.context)

    def test_instruction_only_delta_preserves_original_files(self):
        """仅补充修复说明时，原文件清单与交付物保持原样。"""
        self.proposal["files"] = []
        repaired = compile_repaired_plan(self.state, "business", TaskRepairProposal.model_validate(self.proposal), self.context)
        task = repaired["task_registry"]["business"]
        original = self.plan["task_registry"]["business"]
        self.assertEqual(task["target_files"], original["target_files"])
        self.assertEqual(task["change_scope"], original["change_scope"])
        self.assertEqual(task["deliverables"], original["deliverables"])

    def test_full_task_rewrite_is_rejected(self):
        """模型不得通过增量协议重写原任务标题或完整说明。"""
        for key in ("title", "description"):
            with self.subTest(key=key), self.assertRaises(ValidationError):
                TaskRepairProposal.model_validate({**self.proposal, key: "重写完整任务"})

    def test_existing_file_repair_appends_instead_of_replacing_responsibility(self):
        """已有文件的修复要求追加到原职责后，不能覆盖原文件说明。"""
        self.proposal["files"][0]["path"] = self.service
        repaired = compile_repaired_plan(self.state, "business", TaskRepairProposal.model_validate(self.proposal), self.context)
        self.assertEqual(repaired["task_registry"]["business"]["change_scope"][0]["description"],
                         "实现业务\n修复补充：补齐原契约中的字段")

    def test_unknown_deliverable_cannot_expand_business_scope(self):
        """模型不能给文件绑定新业务职责。"""
        self.proposal["files"][0]["deliverableIds"] = ["another-endpoint"]
        with self.assertRaisesRegex(ValueError, "未知"):
            compile_repaired_plan(self.state, "business", TaskRepairProposal.model_validate(self.proposal), self.context)

    def test_add_operation_rejected_for_existing_po(self):
        """已经存在的 PO 必须使用 modify，避免另建文件绕过现有实现。"""
        self.proposal["files"][0]["operation"] = "add"
        with self.assertRaisesRegex(ValueError, "磁盘不一致"):
            compile_repaired_plan(self.state, "business", TaskRepairProposal.model_validate(self.proposal), self.context)

    def test_path_traversal_and_template_files_rejected(self):
        """扩大权限也不能读取或修改工作区外文件、通配目录和模板公共代码。"""
        for name in ("backend/src/../../secret.java", "backend/src/main/java/*.java",
                     "backend/src/main/java/com/cmbchina/backend/common/AuthConstants.java",
                     "backend/src/main/java/com/example/Application.java"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                repair_file(self.workspace, self.plan["task_registry"]["business"], name)

    def test_stale_source_and_consumed_draft_cannot_be_confirmed(self):
        """草稿签名覆盖相关源码，已消费草稿和文件变化都不能重复执行。"""
        request = BuildTaskRepairInput(action="generate", workspaceRoot=self.workspace,
            sourceRunId="source", sourceThreadId="thread", sessionId="session", taskId="business")
        _, signatures = source_files(self.workspace, self.plan["task_registry"]["business"], [self.po])
        draft = save_draft(request, {"status": "pending", "sourceRunId": "source", "sourceThreadId": "thread",
            "sessionId": "session", "taskId": "business", "sourceSignatures": signatures,
            "basePlanDigest": build_task_plan_sha256(self.plan)})
        confirmed = request.model_copy(update={"action": "confirm", "draftDigest": draft["draftDigest"]})
        validate_draft(confirmed, self.state, load_draft(confirmed))
        (Path(self.workspace) / self.po).write_text("class Changed {}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "源码已变化"):
            validate_draft(confirmed, self.state, draft)
        with self.assertRaisesRegex(ValueError, "过期或已消费"):
            validate_draft(confirmed, self.state, {**draft, "status": "confirmed"})

    def test_unstarted_handoff_can_rollback_without_overwriting_later_plan(self):
        """仅回滚匹配的未启动计划；并发确认的后续计划不被覆盖。"""
        request = BuildTaskRepairInput(action="generate", workspaceRoot=self.workspace,
            sourceRunId="source", sourceThreadId="thread", sessionId="session", taskId="business")
        path = build_task_plan_json_path(self.state)
        changed = {**self.plan, "task_repair_confirmation": {"task_id": "business"}}
        write_json_atomic(path, changed)
        rollback_repair_plan(request, self.plan, build_task_plan_sha256(changed), {"status": "pending"})
        self.assertEqual(load_build_task_plan_json(path), self.plan)
        later = {**changed, "task_repair_confirmation": {"task_id": "later"}}
        write_json_atomic(path, later)
        rollback_repair_plan(request, self.plan, build_task_plan_sha256(changed), {"status": "pending"})
        self.assertEqual(load_build_task_plan_json(path), later)


class TaskRepairProtocolTests(unittest.IsolatedAsyncioTestCase):
    """未确认或夹带任务正文的请求必须拒绝，并提供完整标准生命周期。"""

    async def test_missing_confirmation_digest_emits_complete_failure_lifecycle(self):
        """无草稿身份不能执行，业务拒绝仍有消息、错误、状态和结束事件。"""
        payload = {"runId": "request", "threadId": "thread", "forwardedProps": {"buildTaskRepair": {
            "action": "confirm", "workspaceRoot": "workspace", "sourceRunId": "source",
            "sourceThreadId": "thread", "sessionId": "session", "taskId": "business"}}}
        frames = [frame async for frame in build_task_repair_stream(payload=payload)]
        events = [json.loads(line[5:].strip()) for frame in frames for line in frame.splitlines() if line.startswith("data:")]
        types = [event["type"] for event in events]
        self.assertEqual(types[0], "RUN_STARTED")
        self.assertEqual(types[-1], "RUN_FINISHED")
        for name in ("TEXT_MESSAGE_START", "CUSTOM", "STATE_SNAPSHOT", "TEXT_MESSAGE_END"):
            self.assertIn(name, types)
        result = next(event["value"] for event in events if event["type"] == "CUSTOM")
        self.assertEqual(result["status"], "failed")

    def test_client_cannot_submit_task_body(self):
        """任务正文只来自服务端草稿，客户端只能提交确认身份。"""
        with self.assertRaises(ValidationError):
            BuildTaskRepairInput(action="generate", workspaceRoot="workspace", sourceRunId="source",
                sourceThreadId="thread", sessionId="session", taskId="business", task={"owner": "database"})
