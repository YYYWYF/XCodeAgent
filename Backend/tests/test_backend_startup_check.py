"""覆盖临时启动、诊断证据和修复链路，不修改用户应用。"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch, MagicMock

from app.services import backend_startup_check as startup
from app.services.backend_startup_diagnostics import startup_root_cause
from app.services.workspace_process_registry import WorkspaceProcessRegistry

READY_SCRIPT = '''
import socket,time
s=socket.socket(); s.bind(('127.0.0.1',0)); s.listen(20)
print('Tomcat started on port(s): '+str(s.getsockname()[1])+' (http)',flush=True)
print('Started Application in 0.1 seconds (JVM running for 0.2)',flush=True)
time.sleep(30)
'''
ROOT_CAUSE = 'java.lang.ClassNotFoundException: org.springframework.boot.context.properties.ConfigurationBeanFactoryMetadata'


class BackendStartupCheckTests(unittest.TestCase):
    """用真实可回收子进程验证就绪条件，Java 和 Maven 工具选择在边界替换。"""

    def setUp(self) -> None:
        """准备独立工程、进程登记与加速后的超时。"""
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.backend = self.root / 'backend'
        self.target = self.backend / 'target'
        self.target.mkdir(parents=True)
        (self.backend / 'pom.xml').write_text('<project/>')
        self.jar = self.target / 'app-1.0-SNAPSHOT.jar'
        self.write_jar(True)
        self.registry = WorkspaceProcessRegistry()
        self.stack.enter_context(patch.object(startup, 'workspace_process_registry', self.registry))
        self.stack.enter_context(patch.object(startup, 'BACKEND_STARTUP_TIMEOUT_SECONDS', 1))
        self.stack.enter_context(patch.object(startup, 'BACKEND_STARTUP_STABILITY_SECONDS', .05))
        self.stack.enter_context(patch.object(startup, '_backend_runtime_environment', return_value=(dict(os.environ), None)))
        self.stack.enter_context(patch.object(startup.shutil, 'which', return_value=sys.executable))

    def write_jar(self, executable: bool) -> None:
        """生成带或不带入口的实际 JAR 清单。"""
        with zipfile.ZipFile(self.jar, 'w') as archive:
            archive.writestr('META-INF/MANIFEST.MF', 'Main-Class: Application\n' if executable else 'Manifest-Version: 1.0\n')

    def check(self, script: str = READY_SCRIPT, run_id: str = 'run') -> dict:
        """保留进程管理与日志真实行为，只用 Python 子进程模拟 Java 服务。"""
        managed = self.registry.managed_process

        def spawn(_argv, **kwargs):
            """替换可执行文件但保留独立端口和进程登记参数。"""
            return managed([sys.executable, '-u', '-c', script], **kwargs)

        with patch.object(self.registry, 'managed_process', side_effect=spawn):
            result = startup.run_backend_startup_check(
                root=self.root, backend_root=self.backend,
                log_root=self.root / '.xcodeagent/runtime/tests',
                run_id=run_id, maven_command='mvn',
            )
        self.assertEqual(self.registry.active_process_ids(self.root), [])
        return result

    def test_success_requires_completion_port_and_cleanup(self) -> None:
        """完整启动通过且主动回收的退出码不混入应用失败。"""
        result = self.check()
        self.assertTrue(result['passed'], result['evidence'])
        self.assertTrue(result['execution']['cleanup_succeeded'])
        self.assertIsNone(result['execution']['returncode'])
        self.assertIn('--server.port=0', result['command'])
        self.assertNotIn(str(self.root), result['command'])

    def test_banner_or_completion_without_listener_never_passes(self) -> None:
        """横幅和单独 Started 日志均不足以放行。"""
        for message in [':: Spring Boot :: (v2.7.2)', 'Started Application in 0.1 seconds']:
            with self.subTest(message=message):
                result = self.check(f'import time; print({message!r},flush=True); time.sleep(30)')
                self.assertFalse(result['passed'])
                self.assertTrue(result['execution']['timed_out'])

    def test_class_missing_captures_root_cause_and_exit_code(self) -> None:
        """复现 xc30 类缺失，将最底层异常传入失败证据。"""
        result = self.check(f'print("Caused by: {ROOT_CAUSE}",flush=True); raise SystemExit(1)')
        self.assertFalse(result['passed'])
        self.assertEqual(result['execution']['returncode'], 1)
        self.assertIn('ConfigurationBeanFactoryMetadata', result['evidence'])
        self.assertIn(ROOT_CAUSE, result['execution']['root_cause'])

    def test_initialization_error_fails_even_if_process_remains_alive(self) -> None:
        """初始化失败无需等进程自行退出或等待整个超时。"""
        result = self.check('import time; print("APPLICATION FAILED TO START",flush=True); time.sleep(30)')
        self.assertFalse(result['passed'])
        self.assertFalse(result['execution']['timed_out'])

    def test_early_exit_and_unreachable_port_do_not_pass(self) -> None:
        """正常退出码也不是持续运行成功，端口无法访问同样不能放行。"""
        self.assertFalse(self.check('raise SystemExit(0)')['passed'])
        with patch.object(startup, '_port_ready', return_value=False):
            self.assertFalse(self.check()['passed'])

    def test_secrets_are_removed_from_full_logs_and_results(self) -> None:
        """既清理结果也清理 Agent 后续读取的完整日志。"""
        secret = 'database-private-password'
        with patch.object(startup, '_backend_runtime_environment', return_value=({**os.environ, 'MYSQL_PWD': secret}, None)):
            result = self.check(f'print("password={secret} token=other-token"); raise SystemExit(1)')
        log = self.root / result['execution']['stdout_log_virtual'].lstrip('/')
        self.assertNotIn(secret, log.read_text())
        self.assertNotIn('other-token', str(result))
        self.assertNotIn(secret, str(result))

    def test_cancelled_run_does_not_terminate_other_preview(self) -> None:
        """停止只影响目标 run，取消后的迟到检测不能重新启动。"""
        with self.registry.managed_process(
            [sys.executable, '-c', 'import time; time.sleep(30)'],
            workspace=self.root, run_id='preview',
        ) as preview:
            self.registry.cancel_run('run')
            result = startup.run_backend_startup_check(
                root=self.root, backend_root=self.backend,
                log_root=self.root / '.xcodeagent/runtime/tests', run_id='run', maven_command='mvn',
            )
            self.assertFalse(result['passed'])
            self.assertIsNone(preview.poll())

    def test_stop_during_startup_reaps_process(self) -> None:
        """取消正在等待就绪的检测，不留后台 Java 进程。"""
        def cancel() -> None:
            """等待进程登记后发出 run 级取消。"""
            deadline = time.monotonic() + 3
            while not self.registry.active_process_ids(self.root) and time.monotonic() < deadline:
                time.sleep(.01)
            self.registry.cancel_run('run')
        worker = threading.Thread(target=cancel)
        worker.start()
        try:
            result = self.check('import time; time.sleep(30)')
        finally:
            worker.join(timeout=3)
        self.assertFalse(result['passed'])

    def test_missing_java_bad_jar_and_bad_config_fail_closed(self) -> None:
        """工具、产物或数据库配置不可用均产生结构化失败。"""
        with patch.object(startup.shutil, 'which', return_value=None):
            self.assertIn('未找到 Java', self.check()['evidence'])
        self.jar.write_bytes(b'broken')
        self.assertIn('JAR 损坏', self.check()['evidence'])
        with patch.object(startup, '_backend_runtime_environment', return_value=({}, '未配置数据库')):
            self.assertIn('未配置数据库', self.check()['evidence'])

    def test_repackage_failure_preserves_command_and_logs(self) -> None:
        """补打包失败仍有 Maven 诊断，并且检测不重新执行单元测试。"""
        self.write_jar(False)
        def repackage(**kwargs):
            """模拟 Maven 失败并写入独立诊断日志。"""
            self.assertTrue(kwargs['skip_tests'])
            stdout = kwargs['runtime_root'] / 'backend-repackage.stdout.log'
            stderr = kwargs['runtime_root'] / 'backend-repackage.stderr.log'
            stdout.write_text('BUILD FAILURE'); stderr.write_text('java.lang.IllegalStateException: no main class')
            return {'argv': ['mvn','spring-boot:repackage'], 'returncode': 1, 'timed_out': False,
                    'stdout_log': str(stdout), 'stderr_log': str(stderr)}
        with patch.object(startup, '_run_backend_repackage', side_effect=repackage):
            result = self.check()
        self.assertEqual(result['failure_category'], 'backend_repackage')
        self.assertIn('no main class', result['evidence'])

    def test_attempts_preserve_previous_failure_logs(self) -> None:
        """修复重测使用新目录，失败来源仍可审计。"""
        failed = self.check('print("broken"); raise SystemExit(1)')
        passed = self.check()
        self.assertTrue(passed['passed'], passed['evidence'])
        self.assertNotEqual(failed['execution']['stdout_log'], passed['execution']['stdout_log'])
        self.assertIn('broken', (self.root / failed['execution']['stdout_log'].lstrip('/')).read_text())

    def test_root_cause_survives_many_stack_frames(self) -> None:
        """冗长堆栈不挤掉最底层异常。"""
        log = 'java.lang.IllegalStateException: wrapper\n' + ' at frame\n' * 900 + 'Caused by: ' + ROOT_CAUSE
        self.assertIn(ROOT_CAUSE, startup_root_cause(log, ''))

    def test_cleanup_failure_cannot_report_passed(self) -> None:
        """就绪后进程无法回收仍应标红，不能留下成功投影。"""
        @contextmanager
        def stuck_process(*args, **kwargs):
            """模拟进程已经就绪但终止失败。"""
            yield MagicMock(poll=lambda: None)
            raise RuntimeError('进程回收失败')
        with patch.object(self.registry, 'managed_process', side_effect=stuck_process), patch.object(
            startup, 'wait_for_startup', return_value=(True, False, 'ready'),
        ):
            result = startup.run_backend_startup_check(
                root=self.root, backend_root=self.backend,
                log_root=self.root / '.xcodeagent/runtime/tests', run_id='run', maven_command='mvn',
            )
        self.assertFalse(result['passed'])
        self.assertFalse(result['execution']['cleanup_succeeded'])

    @unittest.skipUnless(os.name == 'posix', 'POSIX 信号回收场景')
    def test_sigterm_ignored_process_is_forcibly_reaped(self) -> None:
        """服务忽略正常退出信号时，宽限期后强制回收完整进程。"""
        result = self.check('import signal; signal.signal(signal.SIGTERM,signal.SIG_IGN)\n' + READY_SCRIPT)
        self.assertTrue(result['passed'], result['evidence'])
        self.assertTrue(result['execution']['cleanup_succeeded'])


if __name__ == '__main__':
    unittest.main()
