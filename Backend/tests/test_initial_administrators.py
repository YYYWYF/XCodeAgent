"""共享向量及磁盘基线证明初始管理员的写入与读取边界。"""

import json
from pathlib import Path
import tempfile
import unittest

from app.services.application_config.initial_administrators import (
    parse_for_initialization, parse_new_initial_administrator,
    validate_admin_change, validate_new_admins,
)
from app.services.application_config.schema import ApplicationConfigError
from app.services.application_config.service import ApplicationConfigService
from app.services.application_config_orchestrator import ApplicationConfigOrchestrator, ApplicationConfigOperationError
from tests.test_application_config_mutation import _write_application, _change


class InitialAdministratorsTests(unittest.TestCase):
    """校验新输入无法通过历史回退绕过正式提交。"""

    def test_shared_vectors(self):
        """与 TS/Java 使用完全相同的 Unicode、首零及非法分隔符向量。"""
        vectors = json.loads((Path(__file__).resolve().parents[2] / 'tests/fixtures/initial-administrators.json').read_text())
        for vector in vectors:
            value = vector['value']
            with self.subTest(value=value):
                if vector['valid']:
                    member = parse_new_initial_administrator(value)
                    self.assertEqual((member.member_id, member.member_name), (vector['memberId'], vector['memberName']))
                    self.assertEqual(validate_new_admins([value]), [value.strip()])
                else:
                    with self.assertRaises(ValueError):
                        validate_new_admins([value])
                if value == 'current-user':
                    with self.assertRaises(ValueError):
                        parse_for_initialization(value)
                else:
                    member = parse_for_initialization(value)
                    self.assertEqual(member.member_id, vector['memberId'] if vector['valid'] else value)

    def test_baseline_and_conflicts(self):
        """仅原样旧项获保留资格，工号冲突拒绝，重复同值去重。"""
        legacy = ['ops@example.com', '00123456', '00123456/']
        self.assertEqual(validate_admin_change(legacy, legacy), legacy)
        self.assertEqual(validate_admin_change(legacy, [legacy[0], ' 00000001/欧阳娜娜 ']), [legacy[0], '00000001/欧阳娜娜'])
        for before, after in [([], ['ops@example.com']), (legacy, ['ops@example.com', 'new']),
                              (legacy, ['00123456', '00123456/张三']),
                              ([], ['00123456/张三', '00123456/李四']),
                              ([], [1]), ([], ['00000001/甲'] * 101)]:
            with self.subTest(after=after), self.assertRaises(ValueError):
                validate_admin_change(before, after)
        self.assertEqual(validate_new_admins(['00123456/张三'] * 2), ['00123456/张三'])

    def test_service_and_orchestrator_use_saved_baseline(self):
        """无关保存保留旧数组；非法变更两入口均拒绝且不落盘。"""
        with tempfile.TemporaryDirectory() as workspace:
            target = _write_application(workspace, datasource='database')
            current = json.loads(target.read_text())
            current.update(appName='测试应用', appIcon='app', theme={'primaryColor': '#123456'})
            current['auth']['enable'] = True
            current['authorization'].update(enabled=True, initialAdministratorSubjects=['ops@example.com', '00123456'])
            target.write_text(json.dumps(current))
            service = ApplicationConfigService(workspace)
            orchestrator = ApplicationConfigOrchestrator(None)
            self.assertEqual(service.preview(changes=[])['authorization'], current['authorization'])
            merged = orchestrator._merge(current, {'appName': '新名称'})
            self.assertEqual(merged['authorization'], current['authorization'])
            self.assertEqual(orchestrator._merge(current, {'authorization.initialAdministratorSubjects': current['authorization']['initialAdministratorSubjects']})['authorization'], current['authorization'])
            for values in [['ops@example.com', 'bad'], ['00123456', '00123456/张三']]:
                with self.assertRaises(ApplicationConfigError):
                    service.apply(changes=[], initial_administrator_subjects=values)
                with self.assertRaises(ApplicationConfigOperationError):
                    orchestrator._merge(current, {'authorization.initialAdministratorSubjects': values})
            self.assertEqual(json.loads(target.read_text()), current)
            updated = service.apply(changes=[], initial_administrator_subjects=['00123456/张三'])
            self.assertEqual(updated['authorization']['initialAdministratorSubjects'], ['00123456/张三'])

    def test_first_enable_is_strict(self):
        """首次开启权限时不存在可沿用的历史资格。"""
        with tempfile.TemporaryDirectory() as workspace:
            target = _write_application(workspace, datasource='database')
            current = json.loads(target.read_text())
            current.update(appName='测试应用', appIcon='app', theme={'primaryColor': '#123456'})
            target.write_text(json.dumps(current))
            service = ApplicationConfigService(workspace)
            with self.assertRaises(ApplicationConfigError):
                service.apply(changes=[_change('authorization.enabled', False, True)], initial_administrator_subjects=['ops@example.com'])
            with self.assertRaises(ApplicationConfigOperationError):
                ApplicationConfigOrchestrator(None)._merge(current, {'authorization.enabled': True, 'authorization.initialAdministratorSubjects': ['ops@example.com']})
            preview = service.preview(changes=[_change('authorization.enabled', False, True)], initial_administrator_subjects=['00123456/张三'])
            self.assertEqual(preview['authorization']['initialAdministratorSubjects'], ['00123456/张三'])
            merged = ApplicationConfigOrchestrator(None)._merge(current, {'authorization.enabled': True, 'authorization.initialAdministratorSubjects': ['00123456/张三']})
            self.assertEqual(merged['authorization']['initialAdministratorSubjects'], ['00123456/张三'])
            self.assertEqual(json.loads(target.read_text()), current)
