"""后端启动检测与现有检查、质量门禁、修复和报告链路的契约测试。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agents.repair_planner.planner import _test_repair_planning_prompt
from app.graph.subgraphs.testing import (
    _check_progress_snapshot_writer, _repair_scoped_tasks, build_project_checks,
    frontend_performance_confirmation, integration_test,
)
from app.services.integration_test_runner import run_integration_checks
from app.services.small_task import build_small_task_packet
from app.services.backend_startup_diagnostics import startup_source_fingerprint
from app.services.test_validation import create_repair_task_plan, evaluate_quality_gate
from app.workspace.test_documents import render_test_report_markdown


def failed_startup() -> dict:
    """构造具有真实异常类型与可读日志路径的启动失败。"""
    root = 'java.lang.ClassNotFoundException: org.springframework.boot.context.properties.ConfigurationBeanFactoryMetadata'
    return {
        'id': 'backend_startup', 'name': '后端启动检查', 'layer': 'backend',
        'passed': False, 'skipped': False, 'required': True, 'blocking': True,
        'command': 'java -jar backend/target/app-SNAPSHOT.jar --server.port=0',
        'evidence': '应用启动失败：' + root, 'failure_category': 'backend_startup',
        'repair_hints': ['Backend/pom.xml', 'Backend/src/main/java/Application.java'],
        'execution': {
            'returncode': 1, 'timed_out': False, 'root_cause': root,
            'cwd': 'Backend/target', 'stdout_tail': root, 'stderr_tail': '',
            'stdout_log_virtual': '/.xcodeagent/runtime/tests/backend_startup/attempt/stdout.log',
            'stderr_log_virtual': '/.xcodeagent/runtime/tests/backend_startup/attempt/stderr.log',
        },
    }


class BackendStartupIntegrationTests(unittest.TestCase):
    """验证新增 ID 沿用现有失败处理与 UI 数据流，不另建修复分支。"""

    def test_runner_orders_startup_after_build_and_is_opt_in(self) -> None:
        """仅测试阶段显式启用；普通构建和单测调用行为不变。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); backend = root / 'backend'; backend.mkdir()
            (backend / 'pom.xml').write_text('<project/>')
            calls = []
            def build(**kwargs):
                """记录实际检查命令顺序。"""
                calls.append(kwargs['check_id'])
                return {'id': kwargs['check_id'], 'passed': True, 'skipped': False}
            def start(**kwargs):
                """模拟启动检测，确认复用已构建目录与运行身份。"""
                self.assertEqual(kwargs['backend_root'], root.resolve() / 'backend')
                self.assertEqual(kwargs['run_id'], 'run')
                calls.append('backend_startup')
                return {**failed_startup(), 'passed': True}
            with (
                patch('app.services.integration_test_runner._maven_command', return_value=['mvn']),
                patch('app.services.integration_test_runner._run_command_result', side_effect=build),
                patch('app.services.integration_test_runner.run_backend_startup_check', side_effect=start),
            ):
                result = run_integration_checks(
                    {'workspace': str(root), 'active_run_id': 'run'}, phase='build',
                    affected_layers={'backend'}, include_backend_startup=True,
                )
                self.assertEqual(calls, ['backend_build', 'backend_startup'])
                self.assertEqual([r['id'] for r in result['test_results']], calls)
                calls.clear()
                run_integration_checks({'workspace': str(root)}, phase='build', affected_layers={'backend'})
                self.assertEqual(calls, ['backend_build'])

    def test_failed_build_missing_project_and_static_skip_startup(self) -> None:
        """前置失败不重复修复，无后端或 Static 不启动 Java。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch('app.services.integration_test_runner.run_backend_startup_check') as start:
                missing = run_integration_checks({'workspace': str(root)}, phase='build', affected_layers={'backend'}, include_backend_startup=True)
                self.assertTrue(missing['test_results'][-1]['skipped'])
                (root / 'pom.xml').write_text('<project/>')
                with patch('app.services.integration_test_runner._maven_command', return_value=['mvn']), patch(
                    'app.services.integration_test_runner._run_command_result', return_value={'id':'backend_build','passed':False},
                ):
                    failed = run_integration_checks({'workspace': str(root)}, phase='build', affected_layers={'backend'}, include_backend_startup=True)
                self.assertTrue(failed['test_results'][-1]['skipped'])
                self.assertTrue(failed['test_results'][-1]['passed'])
                with patch('app.services.integration_test_runner._configured_datasource_type', return_value='static'):
                    static = run_integration_checks({'workspace': str(root)}, phase='build', affected_layers={'backend'}, include_backend_startup=True)
                self.assertFalse(static['test_results'][-1]['required'])
                start.assert_not_called()

    def test_graph_enables_check_and_failure_reaches_actual_repair_packet(self) -> None:
        """根因、配置线索与日志跨越门禁、规划和实际修复包后仍完整。"""
        failure = failed_startup()
        with patch('app.graph.subgraphs.testing.run_integration_checks', return_value={'test_results':[failure]}) as runner:
            state = build_project_checks({}, {})
        self.assertTrue(runner.call_args.kwargs['include_backend_startup'])
        report = evaluate_quality_gate(test_results=state['test_results'])
        self.assertFalse(report['passed'])
        request = report['revision_requests'][0]
        self.assertEqual(request['owner'], 'backend')
        tasks = _repair_scoped_tasks(state)
        self.assertIn('Backend', tasks[0]['allowed_paths'])
        self.assertIn('Backend/pom.xml', tasks[0]['target_files'])
        plan = create_repair_task_plan(revision_requests=report['revision_requests'], agent_note='', scoped_tasks=tasks)
        packet = build_small_task_packet(plan['tasks'][0], {'test_report': {'huge': 'x' * 20000}}, source='integration_test.small_task')
        self.assertIn('ConfigurationBeanFactoryMetadata', packet['backendStartupFailure']['rootCause'])
        self.assertEqual(packet['backendStartupFailure']['stdoutLog'], failure['execution']['stdout_log_virtual'])
        prompt = _test_repair_planning_prompt(test_report=report, revision_requests=report['revision_requests'], build_task_plan=None)
        self.assertIn('ConfigurationBeanFactoryMetadata', prompt)
        self.assertIn(failure['execution']['stdout_log_virtual'], prompt)
        self.assertEqual(frontend_performance_confirmation(state)['integration_next_action'], 'skip_frontend_performance')

    def test_snapshots_and_markdown_preserve_order_and_failure(self) -> None:
        """实时及报告均在后端构建后显示启动检查。"""
        snapshots = []
        with patch('app.graph.subgraphs.testing.get_stream_writer', return_value=snapshots.append):
            reporter = _check_progress_snapshot_writer()
        reporter({'check': failed_startup(), 'status':'failed'})
        reporter({'check': {'id':'backend_build','name':'后端构建检查','required':True}, 'status':'passed'})
        self.assertEqual([c['id'] for c in snapshots[-1]['checks']], ['backend_build','backend_startup'])
        with tempfile.TemporaryDirectory() as directory:
            markdown = render_test_report_markdown({'workspace': directory}, {'checks': [failed_startup()]})
        self.assertLess(markdown.index('后端构建检查'), markdown.index('| 后端启动检查'))
        self.assertIn('ConfigurationBeanFactoryMetadata', markdown)

    def test_fresh_retry_drops_previous_startup_cache(self) -> None:
        """失败修复后返回测试，不能复用之前的构建和启动状态。"""
        with patch('app.graph.subgraphs.testing._testing_subgraph') as graph, patch(
            'app.services.development_artifacts.require_test_entry',
        ):
            graph.invoke.return_value = {'quality_gate_passed': True, 'integration_next_action': 'review_phase_confirmation'}
            integration_test({
                'integration_build_checks_completed': False,
                'integration_build_results': [failed_startup()],
                'frontend_performance_decision': 'skip',
            })
            supplied = graph.invoke.call_args.args[0]
        self.assertEqual(supplied['integration_build_results'], [])
        self.assertFalse(supplied['integration_build_checks_completed'])

    def test_source_change_invalidates_confirmed_startup_cache(self) -> None:
        """等待性能确认期间修改 POM，恢复必须重新构建并执行启动检查。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pom = root / 'pom.xml'; pom.write_text('<project/>')
            cached = [{**failed_startup(), 'passed': True, 'source_fingerprint': startup_source_fingerprint(root)}]
            state = {'workspace': directory, 'integration_build_results': cached, 'integration_build_checks_completed': True}
            with patch('app.graph.subgraphs.testing.run_integration_checks', return_value={'test_results':cached}) as runner:
                build_project_checks(state, {})
                runner.assert_not_called()
                pom.write_text('<project>changed</project>')
                build_project_checks(state, {})
                runner.assert_called_once()

    def test_repaired_attempt_rebuilds_and_unlocks_gate(self) -> None:
        """同一失败经修复后重新执行构建和启动，不靠 Agent 自报通过。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / 'pom.xml').write_text('<project/>')
            calls = []
            def build(**kwargs):
                """每轮都记录真实 runner 的构建调用。"""
                calls.append('build')
                return {'id':'backend_build','name':'后端构建检查','passed':True,'skipped':False}
            def start(**kwargs):
                """第一轮失败、第二轮模拟修复后的独立启动成功。"""
                calls.append('startup')
                return {**failed_startup(), 'passed': len(calls) > 2}
            with patch('app.services.integration_test_runner._maven_command', return_value=['mvn']), patch(
                'app.services.integration_test_runner._run_command_result', side_effect=build,
            ), patch('app.services.integration_test_runner.run_backend_startup_check', side_effect=start):
                for expected in (False, True):
                    checks = run_integration_checks({'workspace':directory}, phase='build', affected_layers={'backend'}, include_backend_startup=True)
                    self.assertEqual(evaluate_quality_gate(test_results=checks['test_results'])['passed'], expected)
            self.assertEqual(calls, ['build','startup','build','startup'])


if __name__ == '__main__':
    unittest.main()
