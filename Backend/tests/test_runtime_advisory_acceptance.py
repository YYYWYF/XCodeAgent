"""Runtime 静态诊断不阻断开发，正式来源错误仍阻断。"""

from unittest import TestCase
from unittest.mock import patch

from app.services.business_acceptance_verifier import BusinessAcceptanceVerifier
from app.services.build_business_retry import restored_source_task_ids


class RuntimeAdvisoryAcceptanceTests(TestCase):
    """验证实际汇总策略和旧失败恢复，不调用模型或执行用户工程。"""

    def _task(self):
        """构造当前 Python 对象验收合同。"""
        return {"id": "objects", "source_refs": {"backend_implementation": "python_runtime"},
                "business_acceptance_checks": [{"id": "objects-check", "kind": "python.objects_contract",
                    "deliverable_id": "objects", "sources": [{"artifact": "api_design"}],
                    "target_paths": ["agent-runtime/src/app/schemas.py"], "expected": {},
                    "required": True, "verification_stage": "build",
                    "verification": {"verifier": "python_objects_contract"}}]}

    def test_static_diagnostics_are_advisory_and_keep_original_evidence(self):
        """未知类型及静态实现差异均保留详情，不使任务失败。"""
        task = self._task()
        for status, reason in [("blocked", "python_evidence_unavailable"), ("failed", "implementation_mismatch")]:
            verifier = BusinessAcceptanceVerifier({"python.objects_contract": lambda *args: {
                "status": status, "evidence": "静态分析无法证明实现", "facts": {"reason_code": reason}}})
            with patch("app.services.business_acceptance_verifier._source_hash_errors", return_value=[]), patch(
                "app.services.business_acceptance_verifiers.runtime_support.runtime_verification_files", return_value={}
            ):
                result = verifier.verify(task, "/workspace", formal_artifacts={"current": True})
            self.assertEqual(result["status"], "advisory")
            self.assertEqual(result["business_acceptance_summary"]["advisory"], 1)
            self.assertEqual(result["business_acceptance_evidence"][0]["status"], status)

    def test_source_error_remains_blocking(self):
        """来源变化不能因放松静态推断而绕过。"""
        with patch("app.services.business_acceptance_verifier._source_hash_errors", return_value=["哈希已变化"]):
            result = BusinessAcceptanceVerifier().verify(self._task(), "/workspace", formal_artifacts={"current": True})
        self.assertEqual(result["status"], "blocked")

    def test_verifier_type_error_is_diagnostic_not_generation_failure(self):
        """验收器内部异常保留类型及位置，不能硬阻断生成或泄露异常参数。"""
        def broken_verifier(files, expected):
            """模拟 memory 静态分析器自身抛出的类型异常。"""
            raise TypeError("private-value-must-not-be-exposed")

        task = self._task()
        check = task["business_acceptance_checks"][0]
        check.update(kind="agent.module_contract", verification={"verifier": "agent_module_contract"})
        with patch("app.services.business_acceptance_verifier._source_hash_errors", return_value=[]), patch(
            "app.services.business_acceptance_verifiers.runtime_support.runtime_verification_files", return_value={}
        ):
            result = BusinessAcceptanceVerifier({"agent.module_contract": broken_verifier}).verify(
                task, "/workspace", formal_artifacts={"current": True})
        self.assertEqual(result["status"], "advisory")
        item = result["business_acceptance_evidence"][0]
        self.assertFalse(item["blocking"])
        self.assertEqual(item["facts"]["reason_code"], "verifier_internal_error")
        self.assertIn("broken_verifier", item["facts"]["location"])
        self.assertNotIn("private-value", str(result))

    def test_input_read_error_is_not_mislabeled_as_verifier_error(self):
        """读取输入失败仍明确阻断，不混入可忽略的分析器错误。"""
        with patch("app.services.business_acceptance_verifier._source_hash_errors", return_value=[]), patch(
            "app.services.business_acceptance_verifiers.runtime_support.runtime_verification_files", side_effect=OSError("unreadable")
        ):
            result = BusinessAcceptanceVerifier().verify(self._task(), "/workspace", formal_artifacts={"current": True})
        self.assertEqual(result["status"], "blocked")

    def test_saved_generic_runtime_error_is_rechecked_on_explicit_retry(self):
        """无原因码的当前失败记录也先重新验证，不能只因旧分类永久无候选。"""
        task = {**self._task(), "status": "failed", "failure_category": "business_acceptance_blocked",
                "business_acceptance_evidence": [{"check_id": "objects-check", "status": "blocked", "facts": {}}]}
        with patch("app.services.build_business_retry._source_hash_errors", return_value=[]), patch(
            "app.services.build_business_retry.verify_business_acceptance", return_value={"status": "advisory"}
        ) as verify:
            self.assertEqual(restored_source_task_ids([task], formal_artifacts={"current": True},
                                                    workspace_root="/workspace"), {"objects"})
            verify.assert_called_once()

    def test_old_static_failure_can_retry_under_advisory_policy(self):
        """原 StrictStr 阻断不再因终止分类被永久保留。"""
        task = {**self._task(), "status": "failed", "failure_category": "business_acceptance_blocked",
                "business_acceptance_evidence": [{"check_id": "objects-check", "status": "blocked",
                    "facts": {"reason_code": "python_evidence_unavailable"}}]}
        with patch("app.services.build_business_retry._source_hash_errors", return_value=[]), patch(
            "app.services.build_business_retry.verify_business_acceptance", return_value={"status": "advisory"}
        ):
            self.assertEqual(restored_source_task_ids([task], formal_artifacts={"current": True},
                                                    workspace_root="/workspace"), {"objects"})
