"""验证平台内置表目录、真实字段、空 Schema 绑定和 API 映射失效链路。"""

import asyncio
from contextlib import ExitStack
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START

from app.config import Settings
from app.graph.state import ProjectState
from app.persistence.checkpoints import close_workflow_checkpointer
from app.persistence.execution_recovery import get_execution
from app.protocols.data_sources import build_data_sources_ag_ui_stream
from app.protocols.execution_recovery import build_execution_recovery_ag_ui_stream
from app.protocols.workflow.request import workflow_run_inputs
from app.protocols.workflow.runtime import build_workflow_ag_ui_stream
from app.services.api_design import api_design_readiness, load_database_columns, load_database_tables
from app.services.automatic_artifact_regeneration import prepare_automatic_regeneration
from app.services.binding_workspace import BindingDraftRequest, BindingTarget, save_binding_draft
from app.services.build_scheduler import resolve_execution_slice
from app.services.business_node_retry import read_retry_hint, retry_action_plan
from app.services.data_sources import change_selected_tables, public_catalog, selected_tables
from app.services.endpoint_design_detail import EndpointDesignPrepareRequest, EndpointDesignSaveRequest, prepare_endpoint_design, save_endpoint_design
from app.services.managed_database_client import ManagedDatabaseClient
from app.services.managed_database_metadata import ManagedMetadataResourceBusyError
from app.services.stage_artifacts import ArtifactStateGraph, load_stage_artifacts
from app.services.stage_prerequisites import StagePrerequisiteError, regeneration_for_retry
from app.services.workflow_input_artifacts import pregraph_artifact_values
from app.workspace.endpoint_design_documents import endpoint_design_paths, technical_plan_sha256
from app.workspace.task_documents import build_task_plan_sha256, write_build_task_plan_json
from tests.managed_database_http_fixture import saas_success
from tests.test_confirmed_build_task_plan_loader import _confirmed_plan
from tests.test_workflow_run_control import _write_ready_workbench_lifecycle


class ManagedDatabaseMetadataTests(unittest.TestCase):
    """使用当前工作区文件和 HTTP 替身，不连接真实 SaaS 或数据库。"""

    def setUp(self) -> None:
        """准备已注册内置库及当前技术规划，替换 SaaS 传输。"""

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        folder = self.root / ".devagentstudio"
        (folder / "plans").mkdir(parents=True)
        self.datasource_key = "12ab34cd56ef"
        self.table = self.datasource_key + "_orders"
        self.application = {
            "schemaVersion": 6, "configRevision": 3, "appName": "订单应用",
            "auth": {"enable": False}, "authorization": {"enabled": False, "initialAdministratorSubjects": []},
            "datasource": {"type": "database", "db": {"useBuiltin": True}},
            "managedDatabase": {"datasourceKey": self.datasource_key, "datasourceId": 2, "status": "REGISTERED"},
        }
        self.application_path = folder / "application.json"
        self.application_path.write_text(json.dumps(self.application))
        self.plan = {"api_contracts": [{"id": "orders", "endpoints": [{"id": "list", "method": "GET", "path": "/orders",
            "parameters": [{"name": "id", "in": "query", "schema": {"type": "integer"}}]}]}]}
        (folder / "plans" / "technical-plan.json").write_text(json.dumps(self.plan))
        self.columns = [
            {"name": "id", "type": "bigint", "nullable": False, "primaryKey": True, "autoIncrement": True, "defaultValue": None, "description": "主键"},
            {"name": "is_deleted", "type": "tinyint(1)", "nullable": False, "primaryKey": False, "autoIncrement": False, "defaultValue": "0", "description": "是否删除"},
        ]
        self.response_datasource_key = self.datasource_key
        self.busy = False
        self.columns_busy = False
        self.metadata_error: tuple[int, dict] | None = None
        self.calls: list[httpx.Request] = []
        self.enterContext(patch.dict("os.environ", {"LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false"}))
        settings = Settings(model_base_url="http://model.invalid", model_api_key="test", model_name="test", managed_database_service_key="test-service-key")
        client = ManagedDatabaseClient(settings, transport=httpx.MockTransport(self.reply))
        stack = self.enterContext(ExitStack())
        stack.enter_context(patch("app.services.managed_database_metadata.Settings.from_env", return_value=settings))
        stack.enter_context(patch("app.services.managed_database_metadata.ManagedDatabaseClient", return_value=client))

    def reply(self, request: httpx.Request) -> httpx.Response:
        """只接受当前应用的元数据 GET，不允许测试意外提交表操作。"""

        self.calls.append(request)
        self.assertEqual(request.method, "GET")
        self.assertIn(f"/datasources/{self.datasource_key}/tables", request.url.path)
        if self.busy or (self.columns_busy and request.url.path.endswith("/columns")):
            return httpx.Response(409, json={"returnCode": "RESOURCE_BUSY", "errorMsg": "not-for-public", "body": {}})
        if self.metadata_error:
            status, payload = self.metadata_error
            return httpx.Response(status, json=payload)
        if request.url.path.endswith("/columns"):
            return saas_success(200, {"datasourceKey": self.response_datasource_key, "tableName": self.table, "description": "订单表", "columns": self.columns})
        return saas_success(200, {"datasourceKey": self.response_datasource_key, "tables": [{"tableName": self.table, "description": "订单表", "columnCount": len(self.columns)}]})

    def test_catalog_and_tables_are_automatic_and_do_not_persist_metadata(self) -> None:
        """来源统一命名，SaaS 表自动可选，真实表清单和字段不复制到本地目录。"""

        self.assertEqual(public_catalog(self.root).sources[0].name, "平台内置数据库")
        before = (self.root / ".devagentstudio/datasource/databases/application-database.json").read_text()
        tables = selected_tables(self.root)
        self.assertEqual(tables, [{"sourceId": "application-database", "schema": "", "table": self.table, "description": "订单表"}])
        metadata = load_database_tables(self.root, "application-database")
        self.assertEqual(metadata["tables"][0]["columnCount"], 2)
        self.assertEqual((self.root / ".devagentstudio/datasource/databases/application-database.json").read_text(), before)
        with self.assertRaisesRegex(ValueError, "无需手动添加或移除"):
            change_selected_tables(self.root, "application-database", [self.table], False)

    def test_columns_keep_real_defaults_and_reject_foreign_app(self) -> None:
        """字段来自实际回读并保留完整表名，错误应用回执不能进入映射。"""

        metadata = load_database_columns(self.root, "application-database", self.table)
        self.assertEqual(metadata["schema"], "")
        self.assertEqual(metadata["columns"][1]["defaultValue"], "0")
        self.assertTrue(metadata["columns"][0]["primaryKey"])
        self.assertTrue(metadata["columns"][1]["required"])
        self.response_datasource_key = "ab1234cd56ef"
        with self.assertRaisesRegex(ValueError, "应用身份"):
            load_database_columns(self.root, "application-database", self.table)

    def test_busy_and_missing_registration_are_errors_not_empty_tables(self) -> None:
        """SaaS 占用和注册缺失不能伪装成没有表或回退本地连接。"""

        public_catalog(self.root)
        self.busy = True
        with self.assertRaisesRegex(ManagedMetadataResourceBusyError, "操作完成后继续") as raised:
            selected_tables(self.root)
        self.assertNotIn("not-for-public", str(raised.exception))
        self.assertTrue(raised.exception.__suppress_context__)
        self.calls.clear()
        self.application["managedDatabase"] = {"status": "REGISTERING"}
        self.application_path.write_text(json.dumps(self.application))
        with self.assertRaisesRegex(ValueError, "尚未确认注册成功"):
            selected_tables(self.root)
        self.assertEqual(self.calls, [])

    def test_only_standard_409_busy_receipt_changes_error_type(self) -> None:
        """绑定冲突、其他 HTTP 状态及缺少 Bee 信封仍保留原错误。"""

        for status, payload, message in (
            (409, {"returnCode": "TABLE_BINDING_CONFLICT", "errorMsg": "private", "body": {}}, "应用归属不可用"),
            (503, {"returnCode": "RESOURCE_BUSY", "errorMsg": "private", "body": {}}, "元数据读取失败"),
            (409, {"returnCode": "RESOURCE_BUSY", "errorMsg": "private"}, "元数据读取失败"),
        ):
            with self.subTest(status=status, payload=payload):
                self.metadata_error = (status, payload)
                with self.assertRaisesRegex(ValueError, message):
                    load_database_tables(self.root, "application-database")

    def confirm_query_mapping(self) -> dict:
        """保存引用真实内置表主键的正式查询映射。"""

        preparation = prepare_endpoint_design(EndpointDesignPrepareRequest(workspaceRoot=str(self.root), apiContractId="orders", endpointId="list"))
        draft = preparation["payload"]["draft"]
        field = draft["fieldMappings"][0]["endpointField"]
        selection = BindingTarget(sourceType="database", sourceId="application-database", schema="", table=self.table)
        draft["fieldMappings"] = []
        draft["sourceBinding"] = selection.model_dump(by_alias=True, exclude_none=True)
        draft["databaseOperation"] = "read"
        draft["databaseQuery"] = {"join": "and", "items": [{"kind": "condition", **draft["sourceBinding"],
            "column": "id", "type": "bigint", "operator": "eq", "right": {"kind": "endpoint", "endpointField": field}}]}
        digest = technical_plan_sha256(self.root)
        save_binding_draft(BindingDraftRequest(workspaceRoot=str(self.root), apiContractId="orders", endpointId="list", draft=draft, selection=selection, technicalPlanHash=digest))
        return save_endpoint_design(EndpointDesignSaveRequest(workspaceRoot=str(self.root), apiContractId="orders", endpointId="list", draft=draft, bindingSelection=selection, technicalPlanHash=digest))

    def test_api_binding_saves_empty_schema_and_detects_rebuilt_field(self) -> None:
        """真实空 Schema 查询映射可暂存、确认，各类引用列漂移均使门禁失效。"""

        result = self.confirm_query_mapping()
        self.assertEqual(result["design"]["sourceBinding"]["schema"], "")
        self.assertTrue(api_design_readiness(self.root, self.plan, target_type="endpoint", target_id="list", api_contract_id="orders")["ready"])
        for key, value in {"type": "varchar(64)", "nullable": True, "primaryKey": False,
                           "autoIncrement": False, "defaultValue": "1"}.items():
            with self.subTest(key=key):
                old_value = self.columns[0][key]
                self.columns[0][key] = value
                gate = api_design_readiness(self.root, self.plan, target_type="endpoint", target_id="list", api_contract_id="orders")
                self.assertEqual(gate["missing_api_designs"][0]["status"], "stale")
                self.assertIn("引用字段 id 已变化", gate["missing_api_designs"][0]["reason"])
                self.columns[0][key] = old_value

    def test_pregraph_defers_busy_from_projection_or_recheck(self) -> None:
        """读投影和发生文件竞争后的再次检查都可延后 BUSY。"""

        def read_columns():
            """使用真实 HTTP 客户端路径回读列。"""
            return load_database_columns(self.root, "application-database", self.table)

        self.columns_busy = True
        self.assertEqual(pregraph_artifact_values(str(self.root), "build", read_columns), {})
        with patch("app.services.workflow_input_artifacts.regeneration_for_retry",
                   side_effect=[None, ManagedMetadataResourceBusyError()]):
            def read_replaced_file():
                """复现首轮预检后文件被并发替换。"""
                Path(self.root, "disappeared").read_bytes()

            self.assertEqual(pregraph_artifact_values(str(self.root), "build", read_replaced_file), {})

    def test_build_busy_persists_and_rechecks_original_node(self) -> None:
        """经真实前置校验与 AG-UI checkpoint，两次繁忙后沿原 Build 恢复。"""

        async def exercise():
            """保存正式 DAG、执行失败、从原入口继续，最后验证真实损坏仍会重生成。"""
            workspace = str(self.root)
            _write_ready_workbench_lifecycle(workspace)
            base = self.application_path.parent
            self.plan.update(artifact_type="technical-plan", confirmation_status="confirmed")
            (base / "plans/technical-plan.json").write_text(json.dumps(self.plan))
            (base / "specs").mkdir()
            for relative in ("specs/requirement-spec.json", "plans/product-plan.json"):
                (base / relative).write_text(json.dumps({"confirmation_status": "confirmed", "pages": []}))
            design = self.confirm_query_mapping()["design"]
            formal = _confirmed_plan()
            unit = "backend:module:orders"
            formal["unit_graph"]["nodes"] = [unit]
            formal["task_registry"]["formal-task"].update(
                unit_id=unit, owner="backend",
                source_refs={"endpoint_designs": [design]},
                deliverables=[{"kind": "backend.endpoint_controller", "target_id": "list"}],
            )
            plan_path = Path(write_build_task_plan_json({"workspace": workspace}, formal))
            digest = build_task_plan_sha256(formal)
            scope = {"type": "endpoint", "targetId": "list", "apiContractId": "orders"}
            stage_state = {"workspace": workspace, "build_execution_scope": scope, "build_run_plan_sha256": digest}
            before = {path: path.read_bytes() for path in (plan_path, *endpoint_design_paths(workspace, "orders", "list"))}
            payload = {"runId": "metadata-run", "threadId": "metadata-thread", "resumeFrom": "build",
                       "request": "保留原始 Build 请求", "buildExecutionScope": scope,
                       "forwardedProps": {"workspaceRoot": workspace, "sessionId": "metadata-session"}}
            entered = []

            def entry(state):
                """模拟已确认计划交接，把相同摘要带入真实节点入口。"""
                return {"build_run_plan_sha256": state.get("build_run_plan_sha256") or digest}

            def build(state):
                """只记录已通过前置检查的输入，避免测试执行代码生成或写库。"""
                entered.append(state)
                return {"status": "completed", "phase": "build"}

            builder = ArtifactStateGraph(ProjectState)
            builder.add_node("workflow_entry", entry)
            builder.add_node("build", build)
            builder.add_edge(START, "workflow_entry")
            builder.add_edge("workflow_entry", "build")
            builder.add_edge("build", END)
            graph = builder.compile(checkpointer=InMemorySaver())

            self.columns_busy = True
            with self.assertRaises(ManagedMetadataResourceBusyError):
                api_design_readiness(workspace, self.plan, target_type="endpoint", target_id="list", api_contract_id="orders")
            with self.assertRaises(ManagedMetadataResourceBusyError):
                regeneration_for_retry(workspace, "build", state=stage_state)
            with self.assertRaises(ManagedMetadataResourceBusyError):
                load_stage_artifacts(stage_state, "build")
            inputs = workflow_run_inputs(payload)
            self.assertEqual(inputs["request"], payload["request"])
            self.assertEqual(inputs["resume_from"], "build")
            self.assertNotIn("project_plan", inputs["resume_values"])
            original_inputs = deepcopy(inputs)
            await prepare_automatic_regeneration(inputs, run_id=payload["runId"], thread_id=payload["threadId"])
            self.assertEqual(inputs, original_inputs)

            async def assert_busy(frames, run_id):
                """核对公开失败、持久化诊断、原始上下文及恢复按钮。"""
                self.assertEqual(json.loads(frames.strip().split("\n\n")[-1].removeprefix("data: "))["type"], "RUN_ERROR")
                self.assertTrue(all(expected in frames for expected in ('"RESOURCE_BUSY"', '"managed_database"')))
                self.assertFalse(any(unexpected in frames for unexpected in (
                    "STAGE_PREREQUISITE_INCOMPLETE", "not-for-public", "test-service-key",
                )))
                source = await get_execution(workspace, run_id)
                self.assertEqual(source.status.value, "failed")
                self.assertEqual(source.thread_id, payload["threadId"])
                self.assertEqual(source.failure.origin.value, "external_dependency")
                self.assertEqual((source.failure.code, source.failure.dependency, source.failure.operation),
                                 ("RESOURCE_BUSY", "managed_database", "build"))
                self.assertTrue(source.failure.replay_compatible)
                hint = await read_retry_hint(source, graph)
                self.assertEqual((hint.node, hint.request), ("build", payload["request"]))
                action = await retry_action_plan(source, graph)
                self.assertEqual(action.reason_code, "MANAGED_DATABASE_RESOURCE_BUSY")
                self.assertEqual((action.primary_action.kind.value, action.primary_action.target_node, action.primary_action.label),
                                 ("retry_business_node", "build", "继续"))
                return source

            async def continue_build(source):
                """经正式恢复协议校验按钮凭据，使用同一测试 Graph 重入 Build。"""
                action = await retry_action_plan(source, graph)
                recovery_payload = {"forwardedProps": {"workspaceRoot": workspace, "executionRecovery": {
                    "action": "execute", "incidentId": action.incident_id,
                    "actionId": action.primary_action.action_id,
                }}}
                with patch("app.protocols.execution_recovery.workflow_graph_for_request", return_value=graph), patch(
                    "app.graph.workflow.workflow_graph_for_request", return_value=graph,
                ):
                    chunks = [frame async for frame in build_execution_recovery_ag_ui_stream(payload=recovery_payload)]
                started = json.loads(chunks[0].strip().removeprefix("data: "))
                self.assertEqual(started["type"], "RUN_STARTED")
                return "".join(chunks), started["runId"]

            source = await assert_busy("".join([frame async for frame in build_workflow_ag_ui_stream(
                graph=graph, payload=payload)]), payload["runId"])
            source = await assert_busy(*await continue_build(source))
            self.assertEqual(entered, [])

            self.columns_busy = False
            frames, _ = await continue_build(source)
            self.assertNotIn('"RESOURCE_BUSY"', frames)
            self.assertEqual(len(entered), 1)
            self.assertEqual(entered[0]["request"], payload["request"])
            self.assertEqual(entered[0]["build_execution_scope"], scope)
            self.assertEqual(entered[0]["build_run_plan_sha256"], digest)
            self.assertEqual(entered[0]["build_task_plan"], formal)
            execution_slice = resolve_execution_slice(build_task_plan=formal, build_execution_scope=scope,
                                                      tasks=list(formal["task_registry"].values()))
            self.assertEqual(execution_slice["reusable_task_ids"], ["formal-task"])
            with self.assertRaisesRegex(StagePrerequisiteError, "任务计划") as replaced:
                load_stage_artifacts({**stage_state, "build_run_plan_sha256": "0" * 64}, "build")
            self.assertIn("任务计划已被替换", replaced.exception.issue.detail)
            self.assertEqual({path: path.read_bytes() for path in before}, before)

            self.columns[0]["primaryKey"] = False
            with self.assertRaises(StagePrerequisiteError) as drift:
                load_stage_artifacts(stage_state, "build")
            self.assertIn("引用字段 id 已变化", drift.exception.issue.detail)
            self.columns[0]["primaryKey"] = True

            for bad_plan in ('{"invalid":', json.dumps({**formal, "confirmation_status": "pending"}), None):
                if bad_plan is None:
                    plan_path.unlink()
                else:
                    plan_path.write_text(bad_plan)
                with self.assertRaises(StagePrerequisiteError) as raised:
                    load_stage_artifacts(stage_state, "build")
                self.assertEqual(raised.exception.issue.key, "build_task_plan")
                action = await retry_action_plan(source, graph)
                self.assertEqual(action.reason_code, "STAGE_PREREQUISITE_INCOMPLETE")
                self.assertEqual(action.primary_action.target_node, "api_design_readiness_gate")
            self.assertTrue(all(call.method == "GET" for call in self.calls))

        async def run_and_close():
            """释放测试中真实创建的 checkpoint 连接。"""
            try:
                await exercise()
            finally:
                await close_workflow_checkpointer()

        asyncio.run(run_and_close())

    def test_builtin_snapshot_cannot_skip_resource_identity_check(self) -> None:
        """内置快照缺少当前数据源 Key 时必须阻断，不能当作外部来源跳过。"""

        from app.services.managed_database_metadata import validate_managed_snapshots

        with patch("app.services.data_sources.database_source_mode", return_value="builtin"):
            with self.assertRaisesRegex(ValueError, "缺少数据源 Key"):
                validate_managed_snapshots(self.root, {"sourceSnapshots": [{
                    "sourceType": "database", "sourceId": "application-database",
                    "details": {"schema": "", "table": self.table, "columns": []},
                }]})

    def test_selected_tables_uses_complete_ag_ui_lifecycle(self) -> None:
        """自动内置表目录仍通过标准数据源产品流返回，不新增 REST 读取链路。"""

        async def collect() -> str:
            """收集一次只读动作的完整输出。"""
            return "".join([part async for part in build_data_sources_ag_ui_stream(action="selected_tables", payload={
                "threadId": "metadata-test", "runId": "metadata-run", "forwardedProps": {"dataSources": {"workspaceRoot": str(self.root)}}})])

        with patch("app.services.preview_runtime_guard.record_product_interaction"):
            output = asyncio.run(collect())
        for event in ["RUN_STARTED", "STATE_SNAPSHOT", "TEXT_MESSAGE_END", "RUN_FINISHED"]:
            self.assertIn(event, output)
        self.assertIn(self.table, output)
        self.assertIn('"status":"completed"', output)
