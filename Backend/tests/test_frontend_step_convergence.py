"""以同快照回放前端无变化写入、步骤验收和安全重读。"""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from app.agents.auto_dedup_backend import AutoDedupFilesystemBackend
from app.agents.frontend.execution_scope import FrontendTaskExecutionScope
from app.agents.frontend.file_backend import FILE_ACCESS, FrontendScopedFilesystemBackend
from app.agents.frontend.read_ledger import FrontendReadLedger
from app.agents.frontend.task_packet import API, PAGE
from app.services.frontend_request_acceptance import page_request_findings
from app.services.business_acceptance_verifiers.typescript_ast import TypeScriptAstError
from tests.test_frontend_build_execution import _API, _PAGE, _create_file, _packet


def _advance(scope, *, defer=False):
    """沿用一步步推进的控制工具输入。"""
    return scope.transition("advance_frontend_step", {
        "task_id": scope.current["id"], "completion_evidence": "已自述完成",
        "defer_to_repair": defer,
    })


def _begin(root, kind=PAGE):
    """启动带真实文件授权的单任务作用域。"""
    scope = FrontendTaskExecutionScope(str(root), [_packet(kind)], Mock())
    scope.transition("begin_frontend_task", {"task_id": kind})
    return scope


class FrontendStepConvergenceTests(unittest.TestCase):
    """覆盖步骤内修改、原错误语义和可重试工程验收。"""

    def test_same_snapshot_write_comparison(self):
        """相同初始文件和工具序列中，只有新后端跳过两次同内容落盘。"""
        original = "export default function Page() { return 1; }\n"
        with TemporaryDirectory() as old_folder, TemporaryDirectory() as new_folder:
            old_root, new_root = Path(old_folder), Path(new_folder)
            old = AutoDedupFilesystemBackend(root_dir=old_root, virtual_mode=True)
            scope = _begin(new_root)
            _advance(scope)
            updated = FrontendScopedFilesystemBackend(scope)
            outcomes = []
            for root, backend in ((old_root, old), (new_root, updated)):
                file = _create_file(root, _PAGE, original)
                writes = 0
                token = FILE_ACCESS.set((scope, _PAGE))
                try:
                    operations = [
                        (backend.edit, (_PAGE, "return 1", "return 1")),
                        (backend.write, (_PAGE, original)),
                        (backend.edit, (_PAGE, "return 1", "return 2")),
                        (backend.edit, (_PAGE, "return 2", "return 1")),
                    ]
                    for invoke, args in operations:
                        # 用固定标记识别写入，避免依赖文件系统时间戳精度。
                        os.utime(file, ns=(1_000_000_000, 1_000_000_000))
                        self.assertIsNone(invoke(*args).error)
                        writes += int(file.stat().st_mtime_ns != 1_000_000_000)
                finally:
                    FILE_ACCESS.reset(token)
                self.assertEqual(file.read_text(), original)
                outcomes.append(writes)
            self.assertEqual(outcomes, [4, 2])
            events = [entry.kwargs for entry in scope.trace.call_args_list
                      if entry.args == ("frontend_file_mutation",)]
            self.assertEqual(sum(event["frontend_step_no_change_edit_count"] for event in events), 2)
            self.assertEqual(sum(event["frontend_step_revert_count"] for event in events), 1)
            self.assertTrue(scope.mutation_notice["warn_revert"])

    def test_edit_errors_real_whitespace_and_step_local_reverts(self):
        """匹配失败维持错误，真实空白保留，跨步骤回退无需提示。"""
        with TemporaryDirectory() as folder:
            root = Path(folder)
            file = _create_file(root, _PAGE, "")
            file.write_bytes(b"// heading\r\nexport default function Page() { return null; }\r\n// heading\n")
            scope = _begin(root)
            with self.assertRaises(ValueError):
                scope.authorize("edit_file", _PAGE)
            _advance(scope)
            backend = FrontendScopedFilesystemBackend(scope)
            # 任意后端调用仍然需要原工具的路径授权。
            with self.assertRaises(PermissionError):
                backend.edit(_PAGE, "foo", "foo")
            with self.assertRaises(ValueError):
                scope.authorize("edit_file", _API)
            token = FILE_ACCESS.set((scope, _PAGE))
            try:
                self.assertIn("not found", backend.edit(_PAGE, "missing", "missing").error)
                self.assertIn("appears 2 times", backend.edit(_PAGE, "// heading", "// heading").error)
                self.assertIsNone(scope.mutation_notice)
                self.assertIsNone(backend.edit(_PAGE, "return null", "return  null").error)
                self.assertIn(b"return  null; }\r\n", file.read_bytes())
                self.assertIsNone(backend.edit(_PAGE, "return  null", "return   null").error)
                self.assertFalse(scope.mutation_notice["revert_detected"])
                _advance(scope)
                self.assertIsNone(backend.edit(_PAGE, "return   null", "return  null").error)
                self.assertFalse(scope.mutation_notice["revert_detected"])
                self.assertIsNone(backend.edit(_PAGE, "return  null", "return   null").error)
                self.assertTrue(scope.mutation_notice["warn_revert"])
                self.assertIsNone(backend.edit(_PAGE, "return   null", "return  null").error)
                self.assertFalse(scope.mutation_notice["warn_revert"])
                _advance(scope)
                with self.assertRaises(ValueError):
                    scope.authorize("edit_file", _PAGE)
            finally:
                FILE_ACCESS.reset(token)

    def test_step_validation_feedback_retry_and_stale_final(self):
        """页面缺失阻断，AST 违规告警，并在最终核对时发现外部版本变化。"""
        with TemporaryDirectory() as folder:
            root = Path(folder)
            scope = _begin(root)
            self.assertEqual(_advance(scope)["step"], "step_2")
            feedback = _advance(scope)
            self.assertEqual(feedback["step"], "step_2")
            self.assertEqual(feedback["acceptance_failures"][0]["rule_id"], "FE-PAGE-STRUCTURE")
            file = _create_file(root, _PAGE, "export default function Page() { fetch('/x'); return null; }")
            self.assertEqual(_advance(scope)["step"], "step_3")
            bad = _advance(scope)
            self.assertEqual(bad["step"], "step_4")
            self.assertNotIn("acceptance_failures", bad)
            self.assertEqual(bad["ast_warnings"][0]["rule_id"], "FE-NO-DIRECT-HTTP")
            self.assertEqual(bad["ast_warnings"][0]["line"], 1)
            previous = scope.checks.results[1]["file_version"]
            file.write_text("export default function Page() { fetch('/again'); return null; }", encoding="utf-8")
            result = scope.transition("complete_frontend_task", {"task_id": PAGE, "status": "completed"})
            self.assertNotEqual(result["ast_warnings"][0]["file_version"], previous)
            self.assertEqual(result["ast_warnings"][0]["step_id"], "step_4")
            self.assertNotIn("next_action", result)
            self.assertEqual(scope.outcomes[PAGE], "completed")
            self.assertFalse(scope.checks.results)

    def test_defer_to_repair_and_api_rules_are_isolated(self):
        """旧收敛参数不改变告警策略，API 封装没有页面规则。"""
        with TemporaryDirectory() as folder:
            root = Path(folder)
            _create_file(root, _PAGE, "export default function Page() { fetch('/x'); return null; }")
            scope = _begin(root)
            _advance(scope)
            _advance(scope)
            self.assertEqual(_advance(scope, defer=True)["step"], "step_4")
            self.assertTrue(scope.transition("complete_frontend_task", {
                "task_id": PAGE, "status": "completed",
            })["ast_warnings"])
            _create_file(root, _API, "export const load = () => fetch('/api');")
            api = FrontendTaskExecutionScope(folder, [_packet(API)])
            with api.task_context():
                self.assertEqual(api.authorize("read_file", _API)[0], _API)
                self.assertEqual(api.step, 0)
                self.assertEqual(api.control_tools(), [])

    def test_structure_unknown_warns_and_missing_entry_still_blocks_final(self):
        """AST 不可判定保留版本与告警；实际入口缺失在最后一步仍阻断完成。"""
        with TemporaryDirectory() as folder:
            root = Path(folder)
            file = _create_file(root, _PAGE, "export default function Page() { return null; }")
            scope = _begin(root)
            _advance(scope)
            with patch("app.services.frontend_step_acceptance._parse_source",
                       side_effect=TypeScriptAstError("unsupported")):
                result = _advance(scope)
            self.assertEqual(result["step"], "step_3")
            warning = result["ast_warnings"][0]
            self.assertEqual(warning["status"], "not_evaluable")
            self.assertTrue(warning["file_version"])
            _advance(scope)
            # 移走真实入口，由最终版本复验检测缺失，避免与 AST 不可判定混淆。
            file.rename(file.with_suffix(".missing"))
            result = scope.transition("complete_frontend_task", {"task_id": PAGE, "status": "completed"})
            self.assertEqual(result["acceptance_failures"][0]["rule_id"], "FE-PAGE-STRUCTURE")
            self.assertNotIn(PAGE, scope.outcomes)



class FrontendReadAndRequestEvidenceTests(unittest.TestCase):
    """验证同版本覆盖、修改后的读取及明确/不确定的规则结果。"""

    def test_read_coverage_and_new_revision(self):
        """重复范围和外部修改分别观测，截断的最后一行不宣称已经覆盖。"""
        with TemporaryDirectory() as folder:
            path = Path(folder) / "source.ts"
            path.write_text("a\nb\nc\n", encoding="utf-8")
            ledger = FrontendReadLedger()
            version = ledger.version(path)
            first = ledger.observe(_PAGE, path, version, {}, "     1\ta\n     2\tb\n     3\tc", 1)
            self.assertTrue(first["full_read"])
            subset = ledger.observe(_PAGE, path, version, {"offset": 1, "limit": 1}, "     2\tb", 2)
            self.assertEqual(subset["duplicate_read_kind"], "covered")
            self.assertFalse(subset["duplicate_read"])
            self.assertEqual(ledger.observe(_PAGE, path, version, {}, "     1\ta", 4)["duplicate_read_kind"], "exact")
            path.write_text("changed\nagain\n", encoding="utf-8")
            self.assertEqual(ledger.observe(_PAGE, path, version, {}, "     1\ta", 4), {})
            version = ledger.version(path)
            truncated = ledger.observe(_PAGE, path, version, {}, "     1\tchanged\n     2\tag"
                                       "\n\n[Output was truncated due to size limits.", 4)
            self.assertEqual(truncated["duplicate_read_kind"], "new_revision")
            self.assertFalse(truncated["full_read"])
            self.assertEqual(ledger.files[_PAGE].ranges, [(0, 1)])
            ledger.clear()
            self.assertFalse(ledger.files)

    def test_rule_results_distinguish_unknown_and_passed(self):
        """真实 ahooks 与展示状态绑定通过，动态链路及未知依赖进入人工核对。"""
        page, api = _PAGE[1:], _API[1:]
        source = """
import { list as load, save } from '@/apis/ordersApi';
import { useRequest as runRequest } from 'ahooks';
import { ProTable, ProFormSelect } from '@ant-design/pro-components';
export default function Page() {
  const { data: rows, loading } = runRequest(load);
  const context = {request: 'ordinary object'};
  return <ProTable dataSource={rows} loading={loading} onChange={() => save()} />;
}
"""
        findings = page_request_findings({page: source}, {api: {"list"}}, page)
        self.assertEqual({item.status for item in findings}, {"passed"})
        unknown = page_request_findings({page: source}, {}, page, query_expected=True)
        self.assertEqual([item.status for item in unknown if item.rule_id == "FE-QUERY-USE-REQUEST"],
                         ["not_evaluable"])
        dynamic = source.replace("<ProTable dataSource={rows} loading={loading} onChange={() => save()} />",
                                 "<ProFormSelect request={context.callback} />")
        results = page_request_findings({page: dynamic}, {api: {"list"}}, page)
        self.assertIn("not_evaluable", {item.status for item in results if item.rule_id == "FE-NO-UI-REQUEST-PROP"})
        spread = source.replace("<ProTable dataSource={rows}", "<ProTable {...context} dataSource={rows}")
        self.assertIn("not_evaluable", {item.status for item in page_request_findings(
            {page: spread}, {api: {"list"}}, page,
        ) if item.rule_id == "FE-NO-UI-REQUEST-PROP"})
