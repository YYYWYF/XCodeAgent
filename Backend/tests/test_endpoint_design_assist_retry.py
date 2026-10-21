"""分步失败缓存与输入身份的同步回归，不启动事件循环或调用数据库。"""

import copy
import unittest
from unittest.mock import patch

from app.domain.endpoint_design_assist import MappingCandidate
from app.domain.endpoint_design_assist_steps import OperationStepResult
from app.services.endpoint_design_assist import load_assist_context, replace_mapping
from app.services.endpoint_design_assist_retry import generation_identity, save_checkpoint, take_checkpoint
from app.services.endpoint_design_assist_validation import complete_mapping
from app.domain.endpoint_design_assist import ReplaceMappingRequest
from tests.test_endpoint_design_assist import fixture, generation_request, patches, price_candidate


class EndpointDesignAssistRetryTests(unittest.TestCase):
    """恢复结果仅限本轮相同输入，不能成为跨版本持久化状态。"""

    def setUp(self):
        """每个缓存用例独立运行，不影响同进程内其他分步服务测试。"""
        from app.services import endpoint_design_assist_retry
        cache = patch.dict(endpoint_design_assist_retry._checkpoints, clear=True)
        cache.start()
        self.addCleanup(cache.stop)

    def test_checkpoint_is_copied_and_consumed_once(self):
        """后续候选变动不污染缓存；同一凭据不能重复启动。"""
        request, preparation, _ = fixture("E:/test")
        items = [MappingCandidate(**price_candidate(request, preparation))]
        questions = ["请核对金额单位"]
        decision = OperationStepResult(operation="update", reason="按契约修改商品")
        token = save_checkpoint("identity", 2, items, questions, decision)
        decision.operation = "delete"
        items[0].value["sourceFields"][0]["column"] = "changed"
        questions.clear()
        cached = take_checkpoint(token, "identity")
        self.assertEqual(cached.step_index, 2)
        self.assertEqual(cached.candidates[0].value["sourceFields"][0]["column"], "price")
        self.assertEqual(cached.questions, ["请核对金额单位"])
        self.assertEqual(cached.operation.operation, "update")
        with self.assertRaisesRegex(ValueError, "过期"):
            take_checkpoint(token, "identity")

    def test_checkpoint_requires_same_inputs_and_expires(self):
        """跨目标、变更要求及过期都不能继续原分步结果。"""
        with patch("app.services.endpoint_design_assist_retry.time.monotonic", return_value=100):
            token = save_checkpoint("identity", 1, [], [])
        with patch("app.services.endpoint_design_assist_retry.time.monotonic", return_value=101):
            with self.assertRaises(ValueError):
                take_checkpoint(token, "other")
        with patch("app.services.endpoint_design_assist_retry.time.monotonic", return_value=701):
            with self.assertRaises(ValueError):
                take_checkpoint(token, "identity")

    def test_context_keeps_constraints_in_baseline_without_credentials(self):
        """所有真实列约束参与过期判断，连接信息仍不会进入模型上下文。"""
        request, preparation, metadata = fixture("E:/test")
        metadata["columns"][1].update(nullable=False, primaryKey=True, autoIncrement=True, defaultValue=None, password="DO_NOT_COPY")
        p1, p2, p3 = patches(preparation, metadata)
        with p1, p2, p3:
            context = load_assist_context(generation_request(request))
            self.assertEqual(context["columns"][1]["autoIncrement"], True)
            self.assertNotIn("DO_NOT_COPY", str(context["columns"]))
            for key, value in (("nullable", True), ("primaryKey", False), ("autoIncrement", False), ("defaultValue", "1")):
                saved = metadata["columns"][1][key]
                metadata["columns"][1][key] = value
                self.assertNotEqual(context["baseline"]["metadataHash"], load_assist_context(generation_request(request))["baseline"]["metadataHash"])
                metadata["columns"][1][key] = saved

    def test_generation_identity_binds_all_inputs_except_retry_token(self):
        """草稿、目标、版本及要求变化均阻断恢复，凭据本身不改变本轮身份。"""
        raw, _, _ = fixture("E:/test")
        request = generation_request(raw)
        baseline = {"draftHash": "a", "metadataHash": "b"}
        original = generation_identity(request, baseline)
        self.assertEqual(original, generation_identity(request.model_copy(update={"retry_token": "a" * 32}), baseline))
        for key, value in (("instruction", "新需求"), ("clarification", "新增补充说明"), ("endpoint_id", "other"), ("technical_plan_hash", "b" * 64)):
            self.assertNotEqual(original, generation_identity(request.model_copy(update={key: value}), baseline))
        changed = copy.deepcopy(baseline)
        changed["metadataHash"] = "changed"
        self.assertNotEqual(original, generation_identity(request, changed))

    def test_retry_cache_is_bounded(self):
        """限制临时缓存数量，不因重复失败无限保留已验证候选。"""
        with patch("app.services.endpoint_design_assist_retry._MAX_CHECKPOINTS", 2):
            first = save_checkpoint("id", 0, [], [])
            second = save_checkpoint("id", 0, [], [])
            third = save_checkpoint("id", 0, [], [])
            with self.assertRaises(ValueError):
                take_checkpoint(first, "id")
            take_checkpoint(second, "id")
            take_checkpoint(third, "id")

    def test_replace_inferred_operation_with_unselected_old_draft(self):
        """未选操作可准备上下文，替换只更新内存并按新操作清理不适用配置。"""
        request, preparation, metadata = fixture("E:/test")
        request["draft"]["databaseOperation"] = None
        original = copy.deepcopy(request["draft"])
        p1, p2, p3 = patches(preparation, metadata)
        with p1, p2, p3:
            context = load_assist_context(generation_request(request))
            mapping = complete_mapping([], {**context, "databaseOperation": "create"})
            applied = replace_mapping(ReplaceMappingRequest(**request, baseline=context["baseline"], mapping=mapping))
        self.assertEqual(applied["draft"]["databaseOperation"], "create")
        self.assertNotIn("databaseQuery", applied["draft"])
        self.assertIn("write", applied["areas"])
        self.assertNotIn("query", applied["areas"])
        self.assertEqual(request["draft"], original)
