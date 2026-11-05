"""覆盖 Rsbuild 编译期及前端 Runtime 代理的短暂未就绪，不实际启动服务。"""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch, call

from app.services.frontend_project_launcher import _dev_server_log_is_ready
from app.services.agent_runtime_health import wait_runtime_proxy_ready
from app.services.project_launcher import _launch_project_preview


class RuntimeProxyReadinessTests(TestCase):
    """验证等待不重启、取消退出及时收口，且就绪证据绑定本轮 Runtime。"""

    def setUp(self):
        """绑定测试时钟、存活进程与取消状态，避免测试产生真实等待。"""
        clock = patch('app.services.agent_runtime_health.time.monotonic', side_effect=range(100))
        sleep = patch('app.services.agent_runtime_health.time.sleep')
        process = patch('app.services.agent_runtime_health.pid_is_running', return_value=True)
        cancelled = patch('app.services.agent_runtime_health.workspace_process_registry.is_run_cancelled', return_value=False)
        for target in (clock, sleep, process, cancelled):
            self.addCleanup(target.stop)
        self.clock, self.sleep, self.process, self.cancelled = [target.start() for target in (clock, sleep, process, cancelled)]

    def wait(self, **kwargs):
        """用固定双端进程及能力身份调用实际代理等待器。"""
        return wait_runtime_proxy_ready('http://127.0.0.1:3000', 'current-launch', database_required=True,
            frontend_pid=101, runtime_pid=102, expected_agent_ids=['assistant'], **kwargs)

    def test_rsbuild_waits_for_build_completion_in_current_log_slice(self):
        """地址早于编译完成时不可放行，历史成功日志也不可用于本轮。"""
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'stdout.log'
            starting = 'Rsbuild v1.0.14\nLocal: http://127.0.0.1:3000/\nstart   Building...\n'
            path.write_text(starting)
            self.assertFalse(_dev_server_log_is_ready(path, 0))
            built = starting + 'ready   Built in 2.23 s (web)\n'
            path.write_text(built)
            self.assertTrue(_dev_server_log_is_ready(path, 0))
            path.write_text(built + starting)
            self.assertFalse(_dev_server_log_is_ready(path, len(built)))

    def test_transient_proxy_failure_waits_then_accepts_current_health(self):
        """代理短暂不可用后恢复时放行，校验路径、启动身份与 Agent 集合不被省略。"""
        with patch('app.services.agent_runtime_health.runtime_health', side_effect=[
            (False, '尚未返回有效响应'), (True, '就绪')
        ]) as health:
            self.assertEqual(self.wait(), (True, '就绪'))
        self.assertEqual(health.call_count, 2)
        health.assert_called_with('http://127.0.0.1:3000', 'current-launch', database_required=True,
            health_path='/__runtime/health', expected_agent_ids=['assistant'])
        self.assertEqual(self.process.call_args_list, [call(101), call(102)] * 2)

    def test_proxy_timeout_retains_last_failure_reason(self):
        """健康检查始终未通过时有界结束，并保留具体失败原因。"""
        with patch('app.services.agent_runtime_health.runtime_health', return_value=(False, 'HTTP 503')):
            ready, message = self.wait(timeout_seconds=3)
        self.assertFalse(ready)
        self.assertIn('超时', message)
        self.assertIn('HTTP 503', message)

    def test_cancelled_or_exited_process_stops_probing(self):
        """取消及任一端退出时立即失败，不再继续发起探测。"""
        with patch('app.services.agent_runtime_health.runtime_health') as health:
            self.cancelled.return_value = True
            self.assertIn('取消', self.wait()[1])
            self.cancelled.return_value = False
            self.process.side_effect = [False]
            self.assertIn('前端进程已退出', self.wait()[1])
            self.process.side_effect = [True, False]
            self.assertIn('Runtime 进程已退出', self.wait()[1])
            health.assert_not_called()

    def test_preview_keeps_services_after_proxy_becomes_ready(self):
        """实际预览编排接入等待器，代理恢复后不走失败停机清理。"""
        runtime = {'status': 'running', 'message': '就绪', 'preview_url': 'http://127.0.0.1:1234',
            'launch_id': 'current-launch', 'database_required': True, 'agent_ids': ['assistant'], 'server': {'pid': 102}}
        frontend = {'status': 'running', 'preview_url': 'http://127.0.0.1:3000', 'server': {'pid': 101}}
        with patch('app.services.project_launcher.runtime_check_plan', return_value={'topology': {}}), \
             patch('app.services.project_launcher.find_backend_project_root', return_value=None), \
             patch('app.services.project_launcher._application_has_no_backend_business', return_value=False), \
             patch('app.services.project_launcher.launch_runtime', return_value=runtime), \
             patch('app.services.project_launcher.launch_frontend_project', return_value=frontend), \
             patch('app.services.agent_runtime_health.wait_runtime_proxy_ready', return_value=(True, '就绪')) as wait, \
             patch('app.services.project_launcher.stop_runtime') as stop_runtime, \
             patch('app.services.project_launcher.stop_frontend_project') as stop_frontend:
            result = _launch_project_preview('/tmp/runtime-proxy-test')
        self.assertEqual(result['status'], 'running')
        wait.assert_called_once()
        stop_runtime.assert_not_called()
        stop_frontend.assert_not_called()
