"""用临时工作区及 MockTransport 覆盖 Builtin 初始化的不可重放边界。"""

import json
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app.config import Settings
from app.graph.nodes.builtin_authorization_bootstrap import builtin_authorization_bootstrap
from app.graph.workflow import route_prepare_build_tasks, route_workflow_start
from app.services.authorization_table_projection import TABLES, compile_authorization_tables
from app.services.authorization_workspace_projector import PO_FILES, XML_FILES, RUNTIME, ensure_projection
from app.services.builtin_authorization_bootstrap import ensure_builtin_build_projection, run_builtin_authorization_bootstrap, refresh_builtin_authorization, _check
from app.services.managed_table_client import ManagedTableClient
from app.services.authorization_projection_sources import SOURCE_RECEIPT, record_materialized_authorization_sources
from app.services.template_reconcile.state_v3 import load_template_state_v3
from app.protocols.workflow.projection import _public_workflow_state, _workflow_node_detail
from app.protocols.workflow.runtime import _runtime_terminal_process_title
from app.protocols.builtin_authorization import build_builtin_authorization_stream
from app.protocols.workflow.request import workflow_run_inputs

KEY = "12ab34cd56ef"
PLAN = {"artifact_type": "technical-plan", "confirmation_status": "confirmed", "authorization_manifest": {"fingerprint": "sha256:test"}}
from tests.managed_database_http_fixture import saas_success


class BuiltinAuthorizationTests(unittest.TestCase):
    """只替换外部 SaaS 和配置读取，保留投影、事务、状态与路由真实执行。"""

    def setUp(self) -> None:
        """创建拥有七个受管文件的工作区及严格四表 SaaS 替身。"""

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.application = {"authorization": {"enabled": True}, "datasource": {"type": "database", "db": {"useBuiltin": True}},
                            "managedDatabase": {"status": "REGISTERED", "datasourceKey": KEY, "datasourceId": 2}}
        for module in ("app.services.builtin_authorization_bootstrap", "app.graph.workflow"):
            replacement = patch(module + ".read_application_config", side_effect=self.read_application)
            replacement.start()
            self.addCleanup(replacement.stop)
        artifacts = {}
        self.originals = {}
        for relative in (*PO_FILES, *XML_FILES):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            content = (f'@TableName("{PO_FILES[relative]}")\nclass Example {{}}' if relative in PO_FILES
                       else '<mapper><select id="roles">SELECT r.role_id FROM role r JOIN role_resource rr ON r.id=rr.role_id JOIN role_member m ON m.role_id=r.id WHERE r.name=\'FROM role\'</select></mapper>')
            path.write_text(content)
            self.originals[relative] = path.read_bytes()
            artifacts['backend:authorization:' + relative.removeprefix('backend/')] = {
                "target": relative, "capabilityId": "authorization", "installedRevision": "2026.11.03.1"}
        state_path = self.root / '.devagentstudio/template-state.json'
        state_path.parent.mkdir(parents=True, exist_ok=True)
        (state_path.parent / 'application.json').write_text('{}')
        state_path.write_text(json.dumps({"schemaVersion": 3, "templateRevision": "2026.11.03.1",
                                         "requested": {}, "effective": {"authorization": {"enabled": True, "config": {}}},
                                         "installedArtifacts": artifacts, "managedContributions": {}}))
        record_materialized_authorization_sources(self.root, load_template_state_v3(self.root))
        self.ddl_path = self.root / 'backend/sql/authorization/schema.sql'
        self.ddl_path.parent.mkdir(parents=True)
        self.ddl = '\n'.join(f"-- 表 {name}\nCREATE TABLE `{name}` (`id` BIGINT NOT NULL AUTO_INCREMENT, `role_id` BIGINT DEFAULT 0 COMMENT 'role; PRIMARY KEY', PRIMARY KEY (`id`), UNIQUE KEY `uk_role_id` (`role_id`)) ENGINE=InnoDB;" for name in TABLES)
        self.ddl_path.write_text(self.ddl)
        self.calls = []
        self.ready = False
        self.operation_status = 'SUCCEEDED'
        self.history = []
        self.drop_response = False
        self.synced_after_success = True
        self.client = ManagedTableClient(Settings(model_base_url='', model_api_key='', model_name='', managed_database_base_url='https://saas.example', managed_database_service_key='test-secret'), transport=httpx.MockTransport(self.respond))

    def read_application(self, *args, **kwargs) -> dict:
        """让路由与服务读取同一份测试权威配置。"""

        return self.application

    def respond(self, request: httpx.Request) -> httpx.Response:
        """模拟 SaaS 的受理、历史、状态和完整四表结构核验。"""

        self.calls.append(request)
        if request.url.path.endswith('/tables/check'):
            return saas_success(200, {"datasourceId": 2, "ready": self.ready,
                "tables": [{"tableName": KEY + '_' + name, "status": 'SYNCED' if self.ready else 'NOT_CREATED', "differences": []} for name in TABLES]})
        if request.url.path.endswith('/batch'):
            if self.drop_response:
                raise httpx.ReadTimeout('sensitive driver data', request=request)
            return saas_success(202, {"operationId": "123", "status": "RUNNING"})
        if request.url.query:
            return saas_success(200, self.history)
        if request.url.path.endswith('/123'):
            if self.operation_status == 'SUCCEEDED' and self.synced_after_success:
                self.ready = True
            return saas_success(200, self.operation())
        raise AssertionError(request.url)

    def operation(self) -> dict:
        """提供仅含安全状态的批次详情，包含部分失败证据。"""

        return {"operationId": "123", "mode": "CREATE_BATCH", "status": self.operation_status,
                "tableNames": [KEY + '_' + name for name in TABLES],
                "executionResult": {"steps": [{"tableName": KEY + '_' + name, "status": 'FAILED' if index == 2 else 'SUCCEEDED'} for index, name in enumerate(TABLES)]}}

    def run_bootstrap(self, **kwargs) -> dict:
        """通过真实服务执行隔离 SaaS 的初始化。"""

        return run_builtin_authorization_bootstrap(str(self.root), PLAN, client=self.client, **kwargs)

    def batch_calls(self) -> list:
        """只统计实际执行 CREATE 的批量提交。"""

        return [request for request in self.calls if request.url.path.endswith('/batch')]

    def refresh(self) -> dict:
        """通过用户刷新入口读取既有批次，不调用初始化入口。"""

        return refresh_builtin_authorization(str(self.root), '123', client=self.client)

    def test_patch_reports_progress_and_refresh_does_not_write_seed(self) -> None:
        """独立补丁按阶段运行，建表刷新只核验原操作，查询失败明确说明未写入。"""

        async def collect(action: str) -> str:
            """收集独立动作的 AG-UI 事件。"""

            payload = {"threadId": "patch-thread", "forwardedProps": {"builtinAuthorization": {
                "action": action, "workspaceRoot": str(self.root)}}}
            return "".join([frame async for frame in build_builtin_authorization_stream(payload)]).replace(" ", "")

        view = {"stage": "schema", "status": "waiting", "message": "正在自动处理。",
                "schemaReady": False, "seedReady": False, "schemaOperationId": "123"}
        with (
            patch("app.protocols.builtin_authorization.patch_view", return_value=view) as read,
            patch("app.protocols.builtin_authorization.ensure_builtin_authorization_task", return_value=view) as ensure,
            patch("app.protocols.builtin_authorization.refresh_patch", return_value={}) as query,
        ):
            first = asyncio.run(collect("run_patch"))
            self.assertIn('"status":"waiting"', first)
            ensure.assert_called_once_with(str(self.root), source="patch")
            query.assert_not_called()
            asyncio.run(collect("get"))
            ensure.assert_called_once()
            self.assertEqual(read.call_count, 1)
            refreshed = asyncio.run(collect("refresh_patch"))
            query.assert_called_once_with(str(self.root))
            ensure.assert_called_once()
            self.assertIn("STATE_SNAPSHOT", refreshed)
            self.assertIn("RUN_FINISHED", refreshed)
            self.assertFalse(self.calls)

    def test_refresh_ag_ui_lifecycle_for_success_and_validation_failure(self) -> None:
        """刷新成功和业务失败均输出完整生命周期，不进入工作流图。"""

        async def collect(payload: dict) -> str:
            """收集本次独立动作的所有标准事件。"""
            return ''.join([frame async for frame in build_builtin_authorization_stream(payload)])

        self.run_bootstrap()
        result = self.refresh()
        payload = {"threadId": "refresh-thread", "runId": "refresh-run", "forwardedProps": {"builtinAuthorization": {
            "action": "refresh", "workspaceRoot": str(self.root), "operationId": "123"}}}
        with patch('app.protocols.builtin_authorization.refresh_builtin_authorization', return_value=result) as query:
            frames = asyncio.run(collect(payload))
            query.assert_called_once_with(str(self.root), '123')
        for event in ('RUN_STARTED', 'TEXT_MESSAGE_START', 'TEXT_MESSAGE_END', 'STATE_SNAPSHOT', 'RUN_FINISHED', 'builtin-authorization'):
            self.assertIn(event, frames)
        self.assertIn('"schemaReady":true', frames.replace(' ', ''))
        payload['forwardedProps']['builtinAuthorization']['action'] = 'execute'
        with patch('app.protocols.builtin_authorization.refresh_builtin_authorization') as query:
            frames = asyncio.run(collect(payload))
            query.assert_not_called()
        self.assertIn('"status":"failed"', frames.replace(' ', ''))
        self.assertIn('RUN_FINISHED', frames)

    def test_manual_refresh_queries_once_and_never_posts_for_nonterminal_or_failed(self) -> None:
        """每次点击仅查询一次原 ID；失败、未知也绝不重发建表或检查在途结构。"""

        self.run_bootstrap()
        for status in ('RUNNING', 'FAILED', 'UNKNOWN'):
            self.operation_status = status
            self.calls.clear()
            result = self.refresh()
            self.assertEqual(result['operation_status'], status)
            self.assertFalse(result['schema_ready'])
            self.assertEqual(len(self.calls), 1)
            self.assertEqual(self.calls[0].method, 'GET')
            self.assertTrue(self.calls[0].url.path.endswith('/123'))

    def test_busy_empty_table_list_is_valid_and_ready_still_requires_four_tables(self) -> None:
        """使用真实 SaaS 在途空表合同回归误判问题，不能放宽最终四表核验。"""

        compiled = compile_authorization_tables(self.root, KEY)
        for ready, status in ((False, 'RUNNING'), (False, 'UNKNOWN'), (True, 'SUCCEEDED')):
            remote = ManagedTableClient(Settings(model_base_url='', model_api_key='', model_name='', managed_database_base_url='https://saas.example', managed_database_service_key='test'),
                transport=httpx.MockTransport(lambda request: saas_success(200, {"datasourceId": 2, "ready": ready, "operationStatus": status, "tables": []})))
            if ready:
                with self.assertRaises(ValueError):
                    _check(remote, {"datasourceKey": KEY, "datasourceId": 2}, compiled)
            else:
                self.assertFalse(_check(remote, {"datasourceKey": KEY, "datasourceId": 2}, compiled).ready)

    def test_refresh_rejects_stale_operation_and_binding_without_network(self) -> None:
        """历史按钮不能查询已替换操作或把新绑定标记成功。"""

        self.run_bootstrap()
        self.calls.clear()
        with self.assertRaises(ValueError):
            refresh_builtin_authorization(str(self.root), '456', client=self.client)
        self.application['managedDatabase']['datasourceId'] = 3
        with self.assertRaises(ValueError):
            self.refresh()
        self.assertEqual(self.calls, [])

    def test_first_create_then_dag_changes_never_recreate(self) -> None:
        """首轮唯一 Batch 请求，后续 Manifest/DAG 变化不影响成功缓存。"""

        result = self.run_bootstrap()
        self.assertEqual(result['operation_status'], 'RUNNING')
        self.assertFalse(result['schema_ready'])
        self.assertFalse(any(request.url.path.endswith('/123') for request in self.calls))
        result = self.refresh()
        self.assertTrue(result['schema_ready'], result)
        self.assertFalse(result['seed_ready'])
        body = json.loads(self.batch_calls()[0].content)
        self.assertEqual(set(body), {'mode', 'sqls'})
        self.assertEqual(body['mode'], 'CREATE')
        self.assertEqual(len(body['sqls']), 4)
        self.assertTrue(all('PRIMARY KEY' in sql for sql in body['sqls']))
        self.assertEqual(self.run_bootstrap()['status'], 'manual_required')
        self.assertEqual(len(self.batch_calls()), 1)
        state = {"workspace": str(self.root), "technical_plan": {**PLAN, 'authorization_manifest': {'fingerprint': 'new'}}}
        self.assertEqual(route_prepare_build_tasks(state), 'builtin_authorization_bootstrap')
        self.assertEqual(route_workflow_start({**state, 'resume_from': 'build'}), 'builtin_authorization_bootstrap')

    def test_shared_key_ready_never_posts(self) -> None:
        """无本地状态时以 SaaS 归属和结构核验建立成功事实。"""

        self.ready = True
        self.assertTrue(self.run_bootstrap()['schema_ready'])
        self.assertEqual(self.batch_calls(), [])

    def test_running_failed_unknown_and_lost_receipt_do_not_replay(self) -> None:
        """202 不等于成功，明确失败与未知受理均不会被普通 Build 重发。"""

        self.operation_status = 'RUNNING'
        self.assertFalse(self.run_bootstrap()['schema_ready'])
        self.assertEqual(json.loads((self.root / RUNTIME / 'state.json').read_text())['operationId'], '123')
        for status in ('RUNNING', 'FAILED', 'UNKNOWN'):
            self.operation_status = status
            self.assertFalse(self.run_bootstrap()['schema_ready'])
        self.assertEqual(len(self.batch_calls()), 1)

    def test_lost_receipt_never_replays_even_explicit_recovery(self) -> None:
        """响应丢失先写 unknown，显式恢复也不能在无唯一事实时重发。"""

        self.drop_response = True
        result = self.run_bootstrap()
        self.assertNotIn('sensitive', json.dumps(result))
        self.assertEqual(json.loads((self.root / RUNTIME / 'state.json').read_text())['status'], 'unknown')
        self.run_bootstrap(explicit_recovery=True)
        self.assertEqual(len(self.batch_calls()), 1)

    def test_succeeded_requires_ready(self) -> None:
        """仅 Receipt 和 SUCCEEDED 不能证明四表已就绪。"""

        self.synced_after_success = False
        self.run_bootstrap()
        self.assertFalse(self.refresh()['schema_ready'])
        self.assertNotEqual(json.loads((self.root / RUNTIME / 'state.json').read_text())['status'], 'schema_ready')

    def test_projection_only_changes_names_and_is_idempotent(self) -> None:
        """DDL 反向还原逐字一致，PO/XML 二次运行及模板原文件恢复均幂等。"""

        compiled = compile_authorization_tables(self.root, KEY)
        for name, sql in zip(TABLES, compiled['sqls']):
            restored = sql.replace('`' + KEY + '_' + name + '`', '`' + name + '`', 1)
            self.assertIn(restored.strip(), self.ddl)
        ensure_projection(self.root, KEY)
        first = {path: (self.root / path).read_bytes() for path in self.originals}
        ensure_projection(self.root, KEY)
        self.assertEqual(first, {path: (self.root / path).read_bytes() for path in first})
        self.assertIn(b'r.role_id', first[XML_FILES[0]])
        self.assertIn(b"'FROM role'", first[XML_FILES[0]])
        for path, content in self.originals.items():
            (self.root / path).write_bytes(content)
        ensure_builtin_build_projection({'workspace': str(self.root)})
        self.assertEqual(first, {path: (self.root / path).read_bytes() for path in first})
        self.assertEqual(self.calls, [])

    def test_unknown_file_edit_rolls_back_all(self) -> None:
        """七文件任一出现未知编辑时，不提交其他文件改动。"""

        ensure_projection(self.root, KEY)
        (self.root / XML_FILES[1]).write_bytes(self.originals[XML_FILES[1]] + b'<!-- user edit -->')
        (self.root / XML_FILES[0]).write_bytes(self.originals[XML_FILES[0]])
        before = {path: (self.root / path).read_bytes() for path in self.originals}
        with self.assertRaises(ValueError):
            ensure_projection(self.root, KEY)
        self.assertEqual(before, {path: (self.root / path).read_bytes() for path in before})

    def test_missing_pk_invalid_binding_and_changed_binding_have_zero_posts(self) -> None:
        """缺失关系表主键或无效应用绑定不能进入 SaaS 提交。"""

        self.ddl_path.write_text(self.ddl.replace('AUTO_INCREMENT', ''))
        self.assertFalse(self.run_bootstrap()['schema_ready'])
        self.assertEqual(self.calls, [])
        self.ddl_path.write_text(self.ddl)
        self.application['managedDatabase']['status'] = 'UNKNOWN'
        self.run_bootstrap()
        self.assertEqual(self.calls, [])

    def test_routes_are_mutually_exclusive_including_old_resume(self) -> None:
        """Builtin 的 Build 与历史恢复入口均不可执行旧脚本。"""

        state = {'workspace': str(self.root), 'technical_plan': PLAN}
        for resume in ('build', 'authorization_bootstrap', 'builtin_authorization_bootstrap'):
            self.assertEqual(route_workflow_start({**state, 'resume_from': resume}), 'builtin_authorization_bootstrap')
        for source in ({'type': 'database', 'db': {'useBuiltin': False}}, {'type': 'static'}, {'type': 'external_api'}):
            self.application['datasource'] = source
            self.assertEqual(route_prepare_build_tasks(state), 'authorization_bootstrap')
        self.application['authorization']['enabled'] = False
        self.assertEqual(route_prepare_build_tasks(state), 'build')

    def test_node_gate_precedes_any_side_effect(self) -> None:
        """没有确认 DAG 的节点必须失败并保持本地和远端无副作用。"""

        result = builtin_authorization_bootstrap({'workspace': str(self.root), 'technical_plan': PLAN})
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['builtin_authorization_bootstrap_result']['failure_category'], 'build_plan_invalid')
        self.assertFalse((self.root / RUNTIME).exists())

    def test_first_projection_rejects_unknown_changes(self) -> None:
        """首次投影也必须匹配安装包摘要，不能把现有编辑自证为模板来源。"""

        path = self.root / XML_FILES[0]
        path.write_bytes(path.read_bytes() + b'<!-- unknown edit -->')
        self.assertFalse(self.run_bootstrap()['schema_ready'])
        self.assertEqual(self.calls, [])
        self.assertFalse((self.root / RUNTIME / 'projection.json').exists())

    def test_binding_and_ddl_drift_never_recreate(self) -> None:
        """Schema 成功后绑定或 DDL 变化必须拒绝，不复用旧成功或自动迁移。"""

        self.run_bootstrap()
        self.assertTrue(self.refresh()['schema_ready'])
        self.application['managedDatabase']['datasourceId'] = 3
        self.assertFalse(self.run_bootstrap()['schema_ready'])
        self.application['managedDatabase']['datasourceId'] = 2
        self.ddl_path.write_text(self.ddl.replace('DEFAULT 0', 'DEFAULT 1'))
        self.assertFalse(self.run_bootstrap()['schema_ready'])
        self.assertEqual(len(self.batch_calls()), 1)

    def test_failed_batch_only_explicit_recovery_can_submit_again(self) -> None:
        """明确失败保留第三表证据，普通进入不重发，显式恢复才再次安全 CREATE。"""

        self.operation_status = 'FAILED'
        self.run_bootstrap()
        self.refresh()
        record = json.loads((self.root / RUNTIME / 'state.json').read_text())
        self.assertEqual(record['steps'][2]['status'], 'FAILED')
        self.run_bootstrap()
        self.assertEqual(len(self.batch_calls()), 1)
        self.run_bootstrap(explicit_recovery=True)
        self.assertEqual(len(self.batch_calls()), 2)

    def test_shared_workspace_pending_history_blocks_create(self) -> None:
        """跨工作区没有 marker 时也必须识别最近在途操作并保存编号。"""

        self.operation_status = 'RUNNING'
        self.history = [self.operation()]
        self.assertFalse(self.run_bootstrap()['schema_ready'])
        self.assertEqual(json.loads((self.root / RUNTIME / 'state.json').read_text())['operationId'], '123')
        self.assertEqual(self.batch_calls(), [])

    def test_known_running_operation_cannot_be_completed_by_ready_check_alone(self) -> None:
        """已受理操作必须查询终态，即使只读四表已存在也不跳过 RUNNING。"""

        self.operation_status = 'RUNNING'
        self.run_bootstrap()
        self.ready = True
        self.assertFalse(self.run_bootstrap()['schema_ready'])
        self.assertEqual(len(self.batch_calls()), 1)

    def test_ddl_rejects_extra_statements_and_fake_primary_key_comments(self) -> None:
        """额外语句、注释中的伪主键及重复逻辑表均不能进入批量 SQL。"""

        for invalid in (self.ddl + '\nDROP TABLE role;', self.ddl.replace('PRIMARY KEY (`id`)', "/* PRIMARY KEY (`id`) */ UNIQUE (`id`)"), self.ddl.replace('`resource`', '`role`')):
            self.ddl_path.write_text(invalid)
            with self.assertRaises(ValueError):
                compile_authorization_tables(self.root, KEY)

    def test_node_degrades_known_failure_and_emits_independent_result(self) -> None:
        """确认门通过后业务失败沿原图策略放行，独立结果仍明确未完成。"""

        with patch('app.graph.nodes.builtin_authorization_bootstrap._build_plan_gate_error', return_value=''):
            self.application['managedDatabase']['status'] = 'UNKNOWN'
            result = builtin_authorization_bootstrap({'workspace': str(self.root), 'technical_plan': PLAN})
        self.assertEqual(result['status'], 'completed')
        self.assertFalse(result['builtin_authorization_bootstrap_result']['schema_ready'])
        self.assertEqual(result['authorization_bootstrap_result'], result['builtin_authorization_bootstrap_result'])
        self.assertEqual(self.calls, [])

    def test_ag_ui_projects_only_safe_schema_and_seed_summary(self) -> None:
        """新节点沿既有 AG-UI 输出状态，不泄露内部双份结果及远端 SQL。"""

        self.run_bootstrap()
        result = self.refresh()
        update = {'authorization_bootstrap_result': result, 'builtin_authorization_bootstrap_result': result}
        detail = _workflow_node_detail('builtin_authorization_bootstrap', update)['data']['authorizationBootstrap']
        self.assertTrue(detail['schemaReady'])
        self.assertFalse(detail['seedReady'])
        self.assertEqual(detail['operationId'], '123')
        self.assertEqual(detail['initializedTables'], [KEY + '_' + name for name in TABLES])
        public = _public_workflow_state(update)
        self.assertNotIn('builtin_authorization_bootstrap_result', public)
        self.assertNotIn('authorization_bootstrap_result', public)
        self.assertNotIn('test-secret', json.dumps(public))
        title = _runtime_terminal_process_title('builtin_authorization_bootstrap', 'completed', update)
        self.assertEqual(title, '权限表已就绪，权限数据待初始化')
        self.assertNotIn('Seed', result['message'])

    def test_failed_recovery_lost_receipt_cannot_reuse_old_failed_id(self) -> None:
        """重试响应丢失后不能查询上次 FAILED 并把未知新提交当作可再次重试。"""

        self.operation_status = 'FAILED'
        self.run_bootstrap()
        self.history = [self.operation()]
        self.drop_response = True
        self.run_bootstrap(explicit_recovery=True)
        record = json.loads((self.root / RUNTIME / 'state.json').read_text())
        self.assertEqual(record['status'], 'unknown')
        self.assertNotIn('operationId', record)
        self.assertEqual(record['previousOperationId'], '123')
        for explicit in (False, True, False, True):
            self.run_bootstrap(explicit_recovery=explicit)
        self.assertEqual(len(self.batch_calls()), 2)

    def test_new_workspace_accepts_committed_projection_without_runtime_marker(self) -> None:
        """新分支可反向证明已提交投影来源，再以 SaaS 核验复用，不能依赖旧 runtime。"""

        ensure_projection(self.root, KEY)
        self.ready = True
        with tempfile.TemporaryDirectory() as directory:
            second = Path(directory).resolve()
            for relative in (*PO_FILES, *XML_FILES, SOURCE_RECEIPT.as_posix(), '.devagentstudio/template-state.json', 'backend/sql/authorization/schema.sql'):
                target = second / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((self.root / relative).read_bytes())
            result = run_builtin_authorization_bootstrap(str(second), PLAN, client=self.client)
            self.assertTrue(result['schema_ready'], result)
            self.assertEqual(self.batch_calls(), [])
